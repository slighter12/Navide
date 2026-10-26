[.testResults[].assertionResults[]
 | select(.fullName == "uses one actual native runtime for concurrent shells, ordered Unicode, resize, reconnect and exits"
      or .fullName == "sidecar death with sessions and a pending request fails visibly, cleans owned trees only and recovers on a fresh runtime"
      or .fullName == "native Claude production-client checkpoint and persisted resume"
      or .fullName == "native Claude production-client selected failure has no rescue")]
| length == 4 and all(.status == "passed")
