import os
import signal
import subprocess
import time
from threading import Event, Lock, Thread
from typing import Optional, Protocol, Sequence, Tuple

import numpy as np
from dynamixel_sdk.group_sync_read import GroupSyncRead
from dynamixel_sdk.group_sync_write import GroupSyncWrite
from dynamixel_sdk.packet_handler import PacketHandler
from dynamixel_sdk.port_handler import PortHandler
from dynamixel_sdk.robotis_def import (
    COMM_SUCCESS,
    DXL_HIBYTE,
    DXL_HIWORD,
    DXL_LOBYTE,
    DXL_LOWORD,
)

# Constants
ADDR_TORQUE_ENABLE = 64
ADDR_GOAL_POSITION = 116
LEN_GOAL_POSITION = 4
ADDR_PRESENT_POSITION = 132
LEN_PRESENT_POSITION = 4
TORQUE_ENABLE = 1
TORQUE_DISABLE = 0
# Additional control table addresses and lengths for current mode and velocities
ADDR_GOAL_CURRENT = 102
LEN_GOAL_CURRENT = 2
ADDR_PRESENT_VELOCITY = 128
LEN_PRESENT_VELOCITY = 4
ADDR_OPERATING_MODE = 11
CURRENT_CONTROL_MODE = 0
POSITION_CONTROL_MODE = 3

# Servo-specific mappings and limits
#
# NOTE on XL330_M288_T: the two pre-existing entries do not share a convention
# (XM430's `1000 / 2.69` is a plain amps->units conversion using that servo's
# 2.69 mA/unit, i.e. it assumes a torque constant of 1; XC330's 1158.73 implies
# 0.863 Nm/A, which its datasheet does not give).  Rather than copy either, the
# XL330 value below is derived from its datasheet and shown as such.  Prefer
# set_current() over set_torque() where the exact torque scale matters.
TORQUE_TO_CURRENT_MAPPING = {
    "XC330_T288_T": 1158.73,
    "XM430_W210_T": 1000 / 2.69,
    # XL330-M288-T: stall 0.52 Nm @ 1.5 A -> Kt = 0.347 Nm/A; unit is 1.0 mA.
    # units per Nm = 1000 mA/A / 0.347 Nm/A
    "XL330_M288_T": 1000 / (0.52 / 1.5),
}

# Servo specifications for current limits (in mA)
SERVO_CURRENT_LIMITS = {
    "XC330_T288_T": 1193,
    "XM430_W210_T": 1263,
    # Read from the Current Limit register (addr 38) of the Franka GELLO's
    # servos, not from a datasheet.  Note 1750 mA is *above* the 1.5 A stall
    # current: saturating there drives the (plastic-geared) servo past stall.
    "XL330_M288_T": 1750,
}


class DynamixelDriverProtocol(Protocol):
    def set_joints(self, joint_angles: Sequence[float]):
        """Set the joint angles for the Dynamixel servos.

        Args:
            joint_angles (Sequence[float]): A list of joint angles.
        """
        ...

    def set_current(self, currents: Sequence[float]):
        """Set motor currents (mA) for current control mode."""
        ...

    def set_torque(self, torques: Sequence[float]):
        """Set joint torques (Nm), mapped to motor currents using servo mappings."""
        ...

    def set_operating_mode(self, mode: int):
        """Set the operating mode (e.g., CURRENT_CONTROL_MODE or POSITION_CONTROL_MODE)."""
        ...

    def verify_operating_mode(self, expected_mode: int):
        """Verify that servos are in the expected operating mode."""
        ...

    def torque_enabled(self) -> bool:
        """Check if torque is enabled for the Dynamixel servos.

        Returns:
            bool: True if torque is enabled, False if it is disabled.
        """
        ...

    def set_torque_mode(self, enable: bool):
        """Set the torque mode for the Dynamixel servos.

        Args:
            enable (bool): True to enable torque, False to disable.
        """
        ...

    def set_torque_ids(self, ids: Sequence[int], enable: bool):
        """Enable/disable torque on a subset of servos, leaving the rest as-is.

        Lets one servo (e.g. the trigger) stay torqued while the arm servos are
        torque-off for a free feel.
        """
        ...

    def get_joints(self) -> np.ndarray:
        """Get the current joint angles in radians.

        Returns:
            np.ndarray: An array of joint angles.
        """
        ...

    def get_positions_and_velocities(self) -> Tuple[np.ndarray, np.ndarray]:
        """Get joint positions (rad) and velocities (rad/s)."""
        ...

    def close(self):
        """Close the driver."""


class FakeDynamixelDriver(DynamixelDriverProtocol):
    def __init__(self, ids: Sequence[int]):
        self._ids = ids
        self._joint_angles = np.zeros(len(ids), dtype=float)
        self._velocities = np.zeros(len(ids), dtype=float)
        self._currents = np.zeros(len(ids), dtype=float)
        self._torque_ids: set = set()

    def set_joints(self, joint_angles: Sequence[float]):
        if len(joint_angles) != len(self._ids):
            raise ValueError(
                "The length of joint_angles must match the number of servos"
            )
        if not self.torque_enabled():
            raise RuntimeError("Torque must be enabled to set joint angles")
        self._joint_angles = np.array(joint_angles, dtype=float)

    def set_current(self, currents: Sequence[float]):
        if len(currents) != len(self._ids):
            raise ValueError("The length of currents must match the number of servos")
        if not self._torque_ids:
            raise RuntimeError("Torque must be enabled to set currents")
        self._currents = np.array(currents, dtype=float)

    def set_torque(self, torques: Sequence[float]):
        # For fake driver, treat torques as currents for storage
        self.set_current(torques)

    def set_operating_mode(self, mode: int):
        pass

    def verify_operating_mode(self, expected_mode: int):
        pass

    def torque_enabled(self) -> bool:
        return len(self._torque_ids) == len(self._ids) and len(self._ids) > 0

    def set_torque_mode(self, enable: bool):
        self.set_torque_ids(self._ids, enable)

    def set_torque_ids(self, ids: Sequence[int], enable: bool):
        if enable:
            self._torque_ids |= set(ids)
        else:
            self._torque_ids -= set(ids)

    def get_joints(self) -> np.ndarray:
        return self._joint_angles.copy()

    def get_positions_and_velocities(self) -> Tuple[np.ndarray, np.ndarray]:
        return self._joint_angles.copy(), self._velocities.copy()

    def get_positions(self) -> np.ndarray:
        return self.get_joints()

    def close(self):
        pass


class DynamixelDriver(DynamixelDriverProtocol):
    def __init__(
        self,
        ids: Sequence[int],
        servo_types: Optional[Sequence[str]] = None,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 1000000,
        max_retries: int = 3,
        use_fake_fallback: bool = True,
    ):
        """Initialize the DynamixelDriver class.

        Args:
            ids (Sequence[int]): A list of IDs for the Dynamixel servos.
            servo_types (Optional[Sequence[str]]): Optional servo model names for torque->current mapping.
            port (str): The USB port to connect to the arm.
            baudrate (int): The baudrate for communication.
            max_retries (int): Maximum number of initialization attempts.
            use_fake_fallback (bool): Whether to fallback to FakeDynamixelDriver on failure.
        """
        self._ids = ids
        self._joint_angles = None
        self._velocities = None
        self._lock = Lock()
        self._port = port
        self._baudrate = baudrate
        self._max_retries = max_retries
        self._use_fake_fallback = use_fake_fallback
        self._is_fake = False
        self._torque_ids: set = set()  # servo ids with torque currently enabled
        self._stop_thread = Event()

        # Optional torque-current mapping
        self._servo_types = list(servo_types) if servo_types is not None else None
        if self._servo_types is not None:
            self.torque_to_current_map = np.array(
                [TORQUE_TO_CURRENT_MAPPING[s] for s in self._servo_types]
            )
            self.current_limits = np.array(
                [SERVO_CURRENT_LIMITS[s] for s in self._servo_types]
            )
        else:
            self.torque_to_current_map = None
            self.current_limits = None

        # Initialize with retry logic
        if not self._initialize_with_retries():
            if self._use_fake_fallback:
                print("Using fake Dynamixel driver")
                self._initialize_fake_driver()
            else:
                raise RuntimeError(
                    "Failed to initialize Dynamixel driver after all retries"
                )

    def _initialize_with_retries(self) -> bool:
        """Initialize the Dynamixel driver with retry logic."""
        for attempt in range(self._max_retries):
            print(
                f"Attempting to initialize Dynamixel driver (attempt {attempt + 1}/{self._max_retries})"
            )

            # Check port availability
            if not self._check_port_availability():
                print("Port is busy, attempting to free it...")
                if not self._kill_processes_using_port():
                    print("Failed to free port, trying to fix permissions...")
                    self._fix_port_permissions()
                time.sleep(2)

            try:
                self._initialize_hardware()
                print(f"Successfully initialized Dynamixel driver on {self._port}")
                return True
            except Exception as e:
                print(f"Failed to initialize Dynamixel driver: {e}")
                if attempt < self._max_retries - 1:
                    print("Retrying in 2 seconds...")
                    time.sleep(2)
                else:
                    print("Max retries reached")

        return False

    def _initialize_hardware(self):
        """Initialize the hardware connection."""
        # Check and prepare port before connection
        self._prepare_port()

        # Initialize the port handler, packet handler, and group sync read/write
        self._portHandler = PortHandler(self._port)
        self._packetHandler = PacketHandler(2.0)
        # Read both velocity and position in one transaction
        self._groupSyncRead = GroupSyncRead(
            self._portHandler,
            self._packetHandler,
            ADDR_PRESENT_VELOCITY,
            LEN_PRESENT_VELOCITY + LEN_PRESENT_POSITION,
        )
        # Separate writers for position and current
        self._groupSyncWrite = GroupSyncWrite(
            self._portHandler,
            self._packetHandler,
            ADDR_GOAL_POSITION,
            LEN_GOAL_POSITION,
        )
        self._groupSyncWriteCurrent = GroupSyncWrite(
            self._portHandler,
            self._packetHandler,
            ADDR_GOAL_CURRENT,
            LEN_GOAL_CURRENT,
        )

        # Open the port and set the baudrate
        if not self._portHandler.openPort():
            raise RuntimeError("Failed to open the port")

        if not self._portHandler.setBaudRate(self._baudrate):
            raise RuntimeError(f"Failed to change the baudrate, {self._baudrate}")

        # Add parameters for each Dynamixel servo to the group sync read
        for dxl_id in self._ids:
            if not self._groupSyncRead.addParam(dxl_id):
                raise RuntimeError(
                    f"Failed to add parameter for Dynamixel with ID {dxl_id}"
                )

        # Disable torque for each Dynamixel servo
        try:
            self.set_torque_mode(self.torque_enabled())
        except Exception as e:
            print(f"port: {self._port}, {e}")

        self._start_reading_thread()

    def _initialize_fake_driver(self):
        """Initialize as a fake driver."""
        self._is_fake = True
        self._fake_joint_angles = np.zeros(len(self._ids), dtype=float)
        self._fake_velocities = np.zeros(len(self._ids), dtype=float)
        self._fake_currents = np.zeros(len(self._ids), dtype=float)

    def set_joints(self, joint_angles: Sequence[float]):
        if len(joint_angles) != len(self._ids):
            raise ValueError(
                "The length of joint_angles must match the number of servos"
            )
        if not self.torque_enabled():
            raise RuntimeError("Torque must be enabled to set joint angles")

        if self._is_fake:
            self._fake_joint_angles = np.array(joint_angles)
            return

        for dxl_id, angle in zip(self._ids, joint_angles):
            # Convert the angle to the appropriate value for the servo
            position_value = int(angle * 2048 / np.pi)

            # Allocate goal position value into byte array
            param_goal_position = [
                DXL_LOBYTE(DXL_LOWORD(position_value)),
                DXL_HIBYTE(DXL_LOWORD(position_value)),
                DXL_LOBYTE(DXL_HIWORD(position_value)),
                DXL_HIBYTE(DXL_HIWORD(position_value)),
            ]

            # Add goal position value to the Syncwrite parameter storage
            dxl_addparam_result = self._groupSyncWrite.addParam(
                dxl_id, param_goal_position
            )
            if not dxl_addparam_result:
                raise RuntimeError(
                    f"Failed to set joint angle for Dynamixel with ID {dxl_id}"
                )

        # Syncwrite goal position
        dxl_comm_result = self._groupSyncWrite.txPacket()
        if dxl_comm_result != COMM_SUCCESS:
            raise RuntimeError("Failed to syncwrite goal position")

        # Clear syncwrite parameter storage
        self._groupSyncWrite.clearParam()

    def set_current(self, currents: Sequence[float]):
        if self._is_fake:
            if len(currents) != len(self._ids):
                raise ValueError(
                    "The length of currents must match the number of servos"
                )
            if not self._torque_ids:
                raise RuntimeError("Torque must be enabled to set currents")
            self._fake_currents = np.array(currents, dtype=float)
            return

        if len(currents) != len(self._ids):
            raise ValueError("The length of currents must match the number of servos")
        # Goal Current is a plain RAM write, harmless on a torque-off servo, so
        # only require that *some* servo is torqued (a mixed armed state is
        # normal: trigger on, arm servos off).
        if not self._torque_ids:
            raise RuntimeError("Torque must be enabled to set currents")

        # Clip currents to servo-specific limits if available
        currents_array = np.array(currents)
        if self.current_limits is not None:
            currents_array = np.clip(
                currents_array, -self.current_limits, self.current_limits
            )

        with self._lock:
            for dxl_id, current in zip(self._ids, currents_array.tolist()):
                current_value = int(current)
                param_goal_current = [
                    DXL_LOBYTE(current_value),
                    DXL_HIBYTE(current_value),
                ]
                if not self._groupSyncWriteCurrent.addParam(dxl_id, param_goal_current):
                    raise RuntimeError(
                        f"Failed to set current for Dynamixel with ID {dxl_id}"
                    )
            dxl_comm_result = self._groupSyncWriteCurrent.txPacket()
            if dxl_comm_result != COMM_SUCCESS:
                raise RuntimeError("Failed to syncwrite goal current")
            self._groupSyncWriteCurrent.clearParam()

    def set_torque(self, torques: Sequence[float]):
        if self.torque_to_current_map is None:
            raise RuntimeError(
                "Torque-to-current mapping is not configured. Provide servo_types to the driver."
            )
        torques_array = np.array(torques)
        currents = (self.torque_to_current_map * torques_array).tolist()
        self.set_current(currents)

    def torque_enabled(self) -> bool:
        return len(self._torque_ids) == len(self._ids) and len(self._ids) > 0

    def set_torque_mode(self, enable: bool):
        self.set_torque_ids(self._ids, enable)

    def set_torque_ids(self, ids: Sequence[int], enable: bool):
        if self._is_fake:
            if enable:
                self._torque_ids |= set(ids)
            else:
                self._torque_ids -= set(ids)
            return

        torque_value = TORQUE_ENABLE if enable else TORQUE_DISABLE
        with self._lock:
            for dxl_id in ids:
                dxl_comm_result, dxl_error = self._packetHandler.write1ByteTxRx(
                    self._portHandler, dxl_id, ADDR_TORQUE_ENABLE, torque_value
                )
                if dxl_comm_result != COMM_SUCCESS or dxl_error != 0:
                    print(dxl_comm_result)
                    print(dxl_error)
                    raise RuntimeError(
                        f"Failed to set torque mode for Dynamixel with ID {dxl_id}"
                    )
                # Track per-id inside the loop so a mid-loop failure leaves the
                # set matching what actually reached the bus.
                if enable:
                    self._torque_ids.add(dxl_id)
                else:
                    self._torque_ids.discard(dxl_id)

    def set_operating_mode(self, mode: int):
        if self._is_fake:
            return
        with self._lock:
            for dxl_id in self._ids:
                dxl_comm_result, dxl_error = self._packetHandler.write1ByteTxRx(
                    self._portHandler, dxl_id, ADDR_OPERATING_MODE, mode
                )
                if dxl_comm_result != COMM_SUCCESS or dxl_error != 0:
                    raise RuntimeError(
                        f"Failed to set operating mode for Dynamixel with ID {dxl_id}"
                    )

    def verify_operating_mode(self, expected_mode: int):
        if self._is_fake:
            return
        with self._lock:
            for dxl_id in self._ids:
                mode, dxl_comm_result, dxl_error = self._packetHandler.read1ByteTxRx(
                    self._portHandler, dxl_id, ADDR_OPERATING_MODE
                )
                if (
                    dxl_comm_result != COMM_SUCCESS
                    or dxl_error != 0
                    or mode != expected_mode
                ):
                    raise RuntimeError(
                        f"Operating mode mismatch for Dynamixel ID {dxl_id} (got {mode}, expected {expected_mode})"
                    )

    def _start_reading_thread(self):
        self._reading_thread = Thread(target=self._read_joint_states)
        self._reading_thread.daemon = True
        self._reading_thread.start()

    def _read_joint_states(self):
        # Continuously read joint angles and velocities
        while not self._stop_thread.is_set():
            time.sleep(0.001)
            with self._lock:
                _joint_angles = np.zeros(len(self._ids), dtype=int)
                _velocities = np.zeros(len(self._ids), dtype=int)
                dxl_comm_result = self._groupSyncRead.txRxPacket()
                if dxl_comm_result != COMM_SUCCESS:
                    print(f"warning, comm failed: {dxl_comm_result}")
                    continue
                for i, dxl_id in enumerate(self._ids):
                    # velocity
                    if self._groupSyncRead.isAvailable(
                        dxl_id, ADDR_PRESENT_VELOCITY, LEN_PRESENT_VELOCITY
                    ):
                        velocity = self._groupSyncRead.getData(
                            dxl_id, ADDR_PRESENT_VELOCITY, LEN_PRESENT_VELOCITY
                        )
                        # sign correction for 32-bit two's complement
                        if velocity > 0x7FFFFFFF:
                            velocity -= 0x100000000
                        _velocities[i] = velocity
                    else:
                        raise RuntimeError(
                            f"Failed to get velocity for Dynamixel with ID {dxl_id}"
                        )
                    # position
                    if self._groupSyncRead.isAvailable(
                        dxl_id, ADDR_PRESENT_POSITION, LEN_PRESENT_POSITION
                    ):
                        angle = self._groupSyncRead.getData(
                            dxl_id, ADDR_PRESENT_POSITION, LEN_PRESENT_POSITION
                        )
                        # sign correction for 32-bit two's complement
                        if angle > 0x7FFFFFFF:
                            angle -= 0x100000000
                        _joint_angles[i] = angle
                    else:
                        raise RuntimeError(
                            f"Failed to get joint angles for Dynamixel with ID {dxl_id}"
                        )
                self._joint_angles = _joint_angles
                self._velocities = _velocities
            # self._groupSyncRead.clearParam()

    def get_positions_and_velocities(self) -> Tuple[np.ndarray, np.ndarray]:
        if self._is_fake:
            return self._fake_joint_angles.copy(), self._fake_velocities.copy()
        while self._joint_angles is None or self._velocities is None:
            time.sleep(0.1)
        positions_in_radians = self._joint_angles.copy() / 2048.0 * np.pi
        velocities_in_units = self._velocities.copy() * 0.229 * 2 * np.pi / 60
        return positions_in_radians, velocities_in_units

    def get_joints(self) -> np.ndarray:
        if self._is_fake:
            return self._fake_joint_angles.copy()

        # Return a copy of the joint_angles array to avoid race conditions
        while self._joint_angles is None:
            time.sleep(0.1)
        _j = self._joint_angles.copy()
        return _j / 2048.0 * np.pi

    def get_positions(self) -> np.ndarray:
        return self.get_joints()

    def _check_port_availability(self) -> bool:
        """Check if the port is available and not being used by other processes."""
        try:
            # Check if port exists
            if not os.path.exists(self._port):
                print(f"Port {self._port} does not exist")
                return False

            # Check for processes using the port
            result = subprocess.run(
                ["lsof", self._port], capture_output=True, text=True
            )

            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if len(lines) > 1:  # Header + processes
                    print(f"Port {self._port} is being used by other processes:")
                    for line in lines[1:]:
                        print(f"  {line}")
                    return False
            return True
        except Exception as e:
            print(f"Error checking port availability: {e}")
            return False

    def _kill_processes_using_port(self) -> bool:
        """Kill OTHER processes using the port -- never this one.

        ``fuser -k`` kills every holder indiscriminately, including our own
        PID if a previous connection in this same process leaked the port
        (e.g. before DynamixelRobot.close() existed): that self-kill is how
        a GUI reconnect used to take the whole process down instead of just
        freeing the port. So PIDs are listed first and our own is filtered
        out before killing.
        """
        try:
            result = subprocess.run(
                ["fuser", self._port], capture_output=True, text=True
            )
            pids = {int(p) for p in result.stdout.split() if p.strip().isdigit()}
            pids.discard(os.getpid())
            if not pids:
                return False
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            print(f"Killed processes using {self._port}: {sorted(pids)}")
            time.sleep(1)  # Give time for processes to terminate
            return True
        except Exception as e:
            print(f"Error killing processes: {e}")
            return False

    def _fix_port_permissions(self) -> bool:
        """Fix port permissions if needed."""
        try:
            result = subprocess.run(
                ["sudo", "chmod", "666", self._port], capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"Fixed permissions for {self._port}")
                return True
            return False
        except Exception as e:
            print(f"Error fixing port permissions: {e}")
            return False

    def _prepare_port(self):
        """Prepare the port for connection by checking availability and fixing issues."""
        if not self._check_port_availability():
            print(f"Port {self._port} is not available, attempting to fix...")
            self._kill_processes_using_port()
            self._fix_port_permissions()

            # Check again after fixing
            if not self._check_port_availability():
                print(f"Warning: Port {self._port} may still have issues")

    def close(self):
        if self._is_fake:
            return

        self._stop_thread.set()
        # Bounded on purpose: _read_joint_states only re-checks _stop_thread
        # between bus transactions, so a wedged txRxPacket() (e.g. a
        # USB-serial hiccup) can leave the thread never getting back to that
        # check. An unbounded join() here would then hang forever -- and
        # since this runs inside CollectionWorker.run()'s teardown (itself
        # wrapped in try/except by the caller, which doesn't help against a
        # call that never raises, just never returns), that hangs the whole
        # worker QThread, which the GUI's closeEvent can wait out (its own
        # wait() has a timeout) but never actually recovers from -- the
        # window closes while the process lingers forever in the
        # background. The reading thread is a daemon (see
        # _start_reading_thread), so giving up here and closing the port
        # anyway is safe: nothing waits on it, and closePort() often
        # unblocks a stuck read as a side effect anyway.
        self._reading_thread.join(timeout=2.0)
        if self._reading_thread.is_alive():
            print(
                f"warning: Dynamixel reading thread on {self._port} did not "
                "stop within 2s (stuck bus read?) -- closing the port anyway"
            )
        self._portHandler.closePort()


def main():
    # Set the port, baudrate, and servo IDs
    ids = [1]

    # Create a DynamixelDriver instance
    try:
        driver = DynamixelDriver(ids)
    except FileNotFoundError:
        driver = DynamixelDriver(ids, port="/dev/cu.usbserial-FT7WBMUB")

    # Test setting torque mode
    driver.set_torque_mode(True)
    driver.set_torque_mode(False)

    # Test reading the joint angles
    try:
        while True:
            joint_angles = driver.get_joints()
            print(f"Joint angles for IDs {ids}: {joint_angles}")
            # print(f"Joint angles for IDs {ids[1]}: {joint_angles[1]}")
    except KeyboardInterrupt:
        driver.close()


if __name__ == "__main__":
    main()  # Test the driver
