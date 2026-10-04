"""Opt-in causal trace inside a disposable backend; delegates every real call."""
import json
from pathlib import Path
import time

from .redacted_diagnostics import redact


def install_trace(root: Path, generation: str) -> None:
    from agent_team_backend.log_readers.watcher import LogWatcher, _Handler
    from agent_team_backend.log_readers.attribution import Attribution
    from agent_team_backend.cli_vendors.opencode import OpencodeLogReader
    from . import isolation

    trace_path = root / f"{generation}-trace.jsonl"

    def emit(stage, **fields):
        with trace_path.open("a", encoding="utf-8") as stream:
            stream.write(redact(json.dumps({"stage": stage, "wallNs": time.time_ns(), **fields}, default=str)) + "\n")

    from agent_team_backend import fs_observer
    if hasattr(fs_observer, "_Emitter"):
        callback = fs_observer._Emitter.events_callback

        def native(emitter, paths, inodes, flags, ids):
            for path, inode, flag, event_id in zip(paths, inodes, flags, ids):
                if "opencode" in str(path):
                    emit("native.filesystem", path=path, inode=inode, flags=hex(flag), event_id=event_id)
            return callback(emitter, paths, inodes, flags, ids)
        fs_observer._Emitter.events_callback = native

    modified = _Handler.on_modified

    def on_modified(handler, event):
        if "opencode.db" in str(event.src_path):
            emit("filesystem", event=event.event_type, path=event.src_path, directory=event.is_directory)
        return modified(handler, event)

    _Handler.on_modified = on_modified
    initialize = LogWatcher.__init__

    def init(watcher, *args, **kwargs):
        session_sink = kwargs.get("session_sink")
        if session_sink:
            async def traced_session(vendor, path):
                if vendor == "opencode":
                    emit("session_sink.enter", vendor=vendor, path=path)
                result = await session_sink(vendor, path)
                if vendor == "opencode":
                    emit("session_sink.return", result=result,
                         live={str(key): {"session_id": state["session_id"], "vendor": state["vendor"]}
                               for key, state in session_sink.__globals__.get("_live_scans", {}).items()})
                return result
            kwargs["session_sink"] = traced_session
        initialize(watcher, *args, **kwargs)
        enqueue = watcher._queue.put_nowait

        def put_nowait(item):
            if item[0] is not None and "opencode.db" in str(item[0]):
                emit("queue.put", path=item[0], scope=item[1])
            return enqueue(item)
        watcher._queue.put_nowait = put_nowait

    LogWatcher.__init__ = init
    realtime = LogWatcher._process_realtime_path

    async def process(watcher, path):
        relevant = "opencode.db" in str(path)
        if relevant:
            emit("reader.enter", path=path)
        result = await realtime(watcher, path)
        if relevant:
            emit("reader.return", result=result)
        return result

    LogWatcher._process_realtime_path = process
    select_reader = LogWatcher._reader_for

    def selected(watcher, path):
        result = select_reader(watcher, path)
        if "opencode.db" in str(path):
            emit("reader.selected", reader=result.vendor if result else None)
        return result

    LogWatcher._reader_for = selected
    query = OpencodeLogReader._query

    def traced_query(reader, path, sql, params=()):
        result = query(reader, path, sql, params)
        emit("sqlite.query", path=path, sql=sql.strip(), rows=None if result is None else len(result))
        return result

    OpencodeLogReader._query = traced_query
    find = OpencodeLogReader.find_sessions_by_marker

    def markers(reader, wanted):
        wanted = list(wanted)
        result = find(reader, wanted)
        emit("marker.result", wanted=wanted, found=result, paths=reader._db_paths())
        return result

    OpencodeLogReader.find_sessions_by_marker = markers
    announce = Attribution.maybe_announce_session

    def binding(attribution, usage):
        if usage.vendor == "opencode":
            emit("attribution.enter", session=usage.session_id, markers=dict(attribution._unbound_markers),
                 panes={pid: {"vendor": reg.vendor, "cwd": reg.cwd, "workspace": reg.workspace_path,
                              "marker": reg.session_marker} for pid, reg in attribution._panes.items()})
        result = announce(attribution, usage)
        if usage.vendor == "opencode":
            emit("attribution.return", result=result)
        return result

    Attribution.maybe_announce_session = binding
    classify = isolation.real_cli_in

    def external(args, env, allowed_roots):
        result = classify(args, env, allowed_roots)
        if result:
            emit("external.refusal", executable=result, argv=args, executed=isolation.executed_words(args))
        return result

    isolation.real_cli_in = external
