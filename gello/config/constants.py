"""Shared runtime constants for the Gello system.

Values here are the "source of truth" for both robot control and the GUI so
that what the operator sees on screen matches what the motors do.
"""

#: Maximum joint error at which pose-match assist may engage (rad).
#: The GUI "pose matching" gauge uses the same threshold so that the gauge
#: being green coincides with the wall starting to pull (GitHub issue #37A).
MATCH_GATE_RAD = 0.5

#: Floor for the match-current cap (mA).
#: This is a *lower bound on the cap*, not a holding current. Some joints
#: need ~430 mA just to hold position, so 200 mA would make convergence
#: impossible if it were an upper limit.
IDLE_MIN_CURRENT = 200.0
