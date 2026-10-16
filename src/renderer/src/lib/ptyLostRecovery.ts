// Whether a pane whose PTY was lost (a backend outage, or the Agent CLI
// Runtime dying under a live backend) is queued for the existing conversation
// resume path. Pure decision logic — App.vue supplies the setting value and the
// visibility snapshot. The caller must capture `onScreenPaneIds` when the loss
// is reported, not when the batch flushes.

export function shouldQueuePtyLostResume(opts: {
  /** Existing auto-resume-on-reconnect setting, already normalized. */
  autoResume: boolean
  /** Panes visible at the moment the loss is reported. */
  onScreenPaneIds: ReadonlySet<string>
  paneId: string
}): boolean {
  return opts.autoResume && opts.onScreenPaneIds.has(opts.paneId)
}
