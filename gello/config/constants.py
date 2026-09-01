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

#: Pose-match aborts when a *roll* joint (J1/J3/J5/J7 on the FR3) is further
#: than this from its target (rad). Roll joints turn without end, so pulling
#: one from far away can wind the cable; bending joints being far apart is
#: just a different pose and is no reason to stop.
#:
#: Deliberately looser than MATCH_GATE_RAD: the gate decides when *teleop* may
#: start, this decides when aligning becomes unsafe for the cable, and the
#: second is the more forgiving question. 0.5 rad stopped alignments that were
#: perfectly fine to run (2026-09-01, measured on the robot).
ROLL_ABORT_RAD = 1.0
