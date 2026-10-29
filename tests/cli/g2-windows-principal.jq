[.testResults[].assertionResults[]
 | select(.fullName == "uses one actual native runtime through the product's pwsh wrapper: descendant cleanup, interrupt and exit"
      or .fullName == "uses one actual native runtime through the product's cmd wrapper: descendant cleanup, interrupt and exit"
      or .fullName == "uses one actual native runtime for kill and recreate cycles with concurrent creates that keep every session isolated")]
| length == 3 and all(.status == "passed")
