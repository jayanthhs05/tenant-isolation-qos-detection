#!/usr/bin/env bash
# NEGATIVE CONTROL, not a violation: a legitimately busy tenant. One well-behaved batch pod in
# tenant-a runs flat out *within* its own 200m limit, colocated with the victim. It drives
# CPU usage/throttle to its limit (exactly what a naive "pressure alone" fairness rule flags)
# but is a single small job, so it should not measurably hurt the victim — fusion's rule, which
# also requires a victim-side QoS breach, must stay quiet.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../fairness/lib.sh"
launch_hogs busy-benign 1 100m 200m 64Mi 128Mi "while True: pass"
