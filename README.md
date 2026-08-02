# FR3 + GELLO LIBERO 데이터 수집기

기존 `fr3-real-teleop` 포크(아래) 위에, FR3 + GELLO 텔레옵으로 **LIBERO 포맷** 모방학습 데이터를 수집하는 PyQt6 GUI를 추가했다. 신규 파일 위주로 추가했고, 실기 테스트로 발견한 버그 몇 건은 아래처럼 고쳤다.

<p align="center">
  <img src="imgs/libero_collector_gui.jpg" width="90%" />
</p>

- **`experiments/collect_libero_gui.py`** -- PyQt6 메인 GUI. 로봇 노드(`launch_nodes.py`)를 GUI 안에서 직접 시작/재시작/중지 (별도 터미널 불필요), 저장된 task 이름을 드롭다운으로 다시 선택하면 언어 지시문뿐 아니라 그 파일의 마지막 세션에서 쓰인 Reset pose/Grip/GELLO wall/에피소드 길이/리셋 대기/데이터셋 구조까지 그대로 복원(--resume 자동 체크; 카메라 선택은 제외 -- 지금 꽂혀 있는 카메라와는 무관), 연결 전에도 카메라 드롭다운 선택 시 실시간 미리보기, 자세 매칭 게이트(조인트별 델타 바), 세션/에피소드 제어, 데이터셋 탐색기(저장 경로 바로 아래 파일만 전체 경로로 표시, 에피소드 목록/삭제/실제 구조 확인), 홈으로 이동 버튼, 세션 종료 시 요약, GUI 내에서 바로 HDF5 원본 업로드 및 LeRobot 포맷 변환/업로드. 오른쪽 위 English/한국어 토글로 창 제목·버튼·다이얼로그·상태 표시까지 UI 전체 언어를 즉시 전환(로그 패널·서브프로세스 출력은 한국어 그대로). 한손 조작용 단축키(Space/Esc/Delete/Enter), 세션 로그를 파일로도 저장. GUI 시작 시 `scripts/runme.sh`(USB latency timer, CPU governor 튜닝)를 자동 실행 -- 터미널 없이 바탕화면 아이콘으로 켜도 `pkexec` GUI 비밀번호 창으로 동작.
- **`gello/libero_gui_worker.py`** -- `record_dataset.py`의 홈복귀→리셋대기→자세게이트→접근램프→기록 상태 머신을 커맨드큐+Qt시그널 방식으로 이식한 백그라운드 `QThread`.
- **`gello/libero_format.py`** -- LIBERO 표준 HDF5(`<task>_demo.hdf5`) writer. 재시작된 `launch_nodes.py` 같은 자식 프로세스가 fork+exec로 파일 fd를 물려받아 계속 잠가버리는 문제를 막기 위해 `FD_CLOEXEC` 설정.
- **`gello/dataset_schema.py`** -- 저장할 데이터 구조를 세션별로 커스터마이징. GUI의 "데이터셋 구조: 기본/사용자 지정" 버튼에서: action space 선택(EE-delta/EE-pose absolute/Joint-angle delta/Joint-angle absolute), action에 그리퍼 포함 여부, 그리퍼 인코딩(-1/+1 robosuite 관례 vs observation과 동일한 0/1), action의 각 열 이름을 개별로 오버라이드(기본값은 observation과 맞춰짐 -- 예: Joint-angle absolute는 `joint1.pos`.."joint7.pos"/`gripper.pos`), 저장할 observation 필드 선택(이미지 2종/joint states/gripper state/EE pos·ori·states), 추가 필드(joint velocities, timestamp), 이미지 해상도(256x256 LIBERO 기본 vs 원본 해상도). "기본값 사용"이 켜져 있으면 나머지 설정과 무관하게 항상 원래 LIBERO 고정 스키마(EE-delta, 그리퍼 -1/+1, obs 전부, 256x256)로 저장됨 -- GELLO는 조인트 공간으로 텔레옵하지만 기본 action은 실현된 EE 궤적에서 계산한 델타 포즈(OSC_POSE 스타일, [-1,1] 정규화)로 LIBERO 관례에 맞춤. 선택은 `~/libero_gui_logs/dataset_schema.json`에 저장되어 다음 실행에도 유지.
- **`scripts/convert_libero_to_lerobot.py`** -- 위 스키마를 실제로 반영해 변환 (파일의 `obs`/`actions` 실물을 읽어 action space·obs 필드·그리퍼 포함 여부·커스텀 열 이름까지 자동 감지, 여러 파일을 함께 변환할 때 스키마가 다르면 변환 시작 전에 에러로 막음). `--resume`으로 이미 Hub에 올라간 데이터셋에 새 task의 에피소드만 이어붙이는 진짜 증분 업로드 지원 -- 기존 task는 재변환/재인코딩하지 않음 (다만 두 명이 동시에 같은 repo에 --resume+push 하면 서로 덮어써서 데이터가 조용히 유실될 수 있음 -- 스크립트 docstring과 GUI 경고 문구 참고). 이미지는 아직 256x256만 지원 -- "원본 해상도 유지"로 수집한 파일은 변환 전 에러로 안내.
- **`scripts/upload_to_hub.py`** -- 변환 없이 원본 HDF5 파일 자체를 Hugging Face Hub 데이터셋 repo에 그대로 업로드 (GUI의 데이터셋 탐색기 "HDF5 업로드..." 버튼). raw HDF5와 LeRobot 변환본을 각각 다른 repo에 올리는 이원화 업로드 과정은 별도 정리해둠.
- **`gello/i18n.py`** -- GUI 오른쪽 위 English/한국어 토글이 쓰는 문자열 테이블. 실시간 로그·서브프로세스(runme.sh, 변환/업로드 스크립트) 출력은 번역 대상이 아니라 한국어 그대로 유지.
- **버그 수정 (기존 파일)**: `GelloAgent.close()`가 leader의 Dynamixel 시리얼 포트를 한 번도 닫지 않아, 같은 프로세스에서 재연결 시 포트가 자기 자신에 의해 점유된 것으로 잡혀 `fuser -k`가 자기 자신을 죽이는 버그를 고침 (`gello/agents/gello_agent.py`, `gello/robots/dynamixel.py`, `gello/dynamixel/driver.py`). 홈 복귀 중 그리퍼가 직전 상태를 그대로 유지하던(안 열리던) 버그도 고침 (`gello/libero_gui_worker.py`의 `_ramp_to`).

실행: `run_libero_collector.sh` 또는 바탕화면 바로가기로 GUI만 켜면, 그 안에서 로봇 노드까지 관리 가능.

## 학습된 정책 실행 (policy client)

`experiments/fr3_policy_client.py` — GPU 머신의 정책 서버(mamba-embeddingvla
`real_deploy/fr3_policy_server.py`)에 관측을 보내고 8-dim 절대 관절각 청크를 받아
실행한다. 이 컴퓨터에서는 모델 연산 없음.

**프로토콜** (HTTP POST JSON, lehome/so101 구조):
- `POST /reset {"instruction": str}` — 에피소드 시작 시 1회 (텍스트 인코딩 + 상태 초기화)
- `POST /infer {observation}` → `{"actions": [[8 floats] × 15]}` (joint1-7 rad + gripper 0..1, 절대값)
  - `observation.state`: `[8]` (관절 rad 7 + gripper 0..1 — `get_observation()` 그대로)
  - `observation.images.agent`/`.wrist`: `{"base64","shape":[256,256,3],"dtype":"uint8"}` —
    **수집과 동일한 `resize_rgb`(center-crop→256², INTER_AREA)를 클라이언트에서 적용** 후 raw base64.
    학습/배포 픽셀 파이프라인이 동일해야 하므로 이 전처리를 생략하면 안 됨 (640×480 raw를
    보내면 서버가 근사 처리하지만 비권장).

**실행 순서** (FR3 컨트롤러 컴퓨터):
```bash
# 0) 통신 테스트 (로봇/카메라 불필요 — 합성 관측으로 서버 왕복 확인)
(lerobot-venv) python experiments/fr3_policy_client.py --dry-run

# 1) 로봇 노드
(pylibfranka-venv) python experiments/launch_nodes.py --robot fr3

# 2) 클라이언트 (상단 CONFIG에서 SERVER_URL/카메라 시리얼 확인)
(lerobot-venv) python experiments/fr3_policy_client.py --max-seconds 30
```

안전장치: 시작 시 수집기와 동일한 램프로 `libero` reset pose 복귀 후 시작, 매 스텝
목표 관절각을 측정치 ±0.15 rad로 클램프 (정책 오동작 시 급가속 차단). Ctrl-C 안전 종료.

---

## Fork notes (`fr3-real-teleop`)

이 포크는 FR3를 ROS 2가 아니라 `pylibfranka`로 직접 구동한다(`gello/robots/franka_fr3.py`).

- **2026-07-17: Franka robot system 5.10.0 대응 완료.** 클라이언트를 libfranka/pylibfranka **0.21.2** 소스 빌드로 마이그레이션(FCI 프로토콜 v10 — libfranka 0.17 클라이언트는 더 이상 호환되지 않음). GIL-release 패치를 0.21.2 소스에 리베이스: `patches/pylibfranka-0.21-gil-release.diff`, 배경과 절차는 [`patches/README.md`](patches/README.md). 공식 PyPI 휠(0.21.2까지)은 여전히 GIL을 놓지 않으므로 패치 소스 빌드가 필수. 실기 검증 완료(그리퍼 `read_once` 중앙값 ~60ms → 6.9ms).

아래부터는 원본(upstream) README.

----

# GELLO: General, Low-Cost, and Intuitive Teleoperation Framework

<p align="center">
  <img src="imgs/title.png" />
</p>

GELLO is a general, low-cost, and intuitive teleoperation framework for robot manipulators. This repository contains all the software components for GELLO. 

For additional resources:
- [Project Website](https://wuphilipp.github.io/gello_site/)
- [Hardware Repository](https://github.com/wuphilipp/gello_mechanical) - STL files and build instructions
- [ROS 2 Support](ros2/README.md)

## Supported Robots
- **I2RT YAM**
- **Franka FR3** (ROS 2 implementation, please refer to the separate documenation in [`ros2/README.md`](ros2/README.md))
- **Franka FER (Panda)**
- **UR**
- **xArm**
- add your own, see [Adding New Robots](#adding-new-robots)

## Quick Start

```bash
git clone https://github.com/wuphilipp/gello_software.git
cd gello_software
```

## Installation

### Option 1: Virtual Environment (Recommended)

First, install uv if you don't have it:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Create and activate a virtual environment:
```bash
uv venv --python 3.11
source .venv/bin/activate  # Run this every time you open a new shell
git submodule init
git submodule update
uv pip install -r requirements.txt
uv pip install -e .
uv pip install -e third_party/DynamixelSDK/python
```

### Option 2: Docker

Install [Docker](https://docs.docker.com/engine/install/ubuntu/), then:

```bash
docker build . -t gello:latest
python scripts/launch.py
```

### ROS 2 Support

> **Note:** GELLO also supports ROS 2 Humble for the Franka FR3 robot. See the [ROS 2-specific README](ros2/README.md) in the `ros2` directory.

## Hardware Configuration

The recommended setup for GELLO is with the I2RT YAM robot arm, using the YAML-based configuration system. This provides the most features and is the best-supported configuration.

### Generate YAML Configuration

For the I2RT YAM robot, you can automatically generate your configuration files. This process calibrates the joint offsets and creates configuration files for both simulation and real hardware.

1.  **Update Motor IDs**: Before generating the config, ensure each Dynamixel motor has a unique ID. Install the [Dynamixel Wizard](https://emanual.robotis.com/docs/en/software/dynamixel/dynamixel_wizard2/) and follow these steps:
    1.  Connect a single motor to the U2D2 controller.
    2.  Open Dynamixel Wizard and scan to detect the motor.
    3.  Change the ID to a unique number (e.g., 1 through 7).
    4.  Repeat for each motor, ensuring they are in order from base to gripper.

2.  **Run the Generation Script**: With the YAM arm in its default build position (see image below), run the script:
    ```bash
    python scripts/generate_yam_config.py
    ```
    Follow the prompts in the terminal. This will create `configs/yam_auto_generated.yaml` for the real robot and `configs/yam_auto_generated_sim.yaml` for the simulation.

<p align="center">
  <img src="imgs/yam_default.JPG" width="42%">
</p>

You can now skip to the [Usage](#usage) section.

### YAML Configuration System

GELLO uses YAML files in `configs/` for configuration. This allows for flexible setup of different robots, environments, and teleoperation parameters. If you have automatically generated your `.yaml` config files with `scripts/generate_yam_config.py`, you probably will not need to modify these confings manually.

#### Sample Configs

Sample configs for the YAM arm and the xarm can be found in `configs`.


#### Configuration Components

- **Robot Config**: Defines robot type, communication parameters, and physical settings.
- **Agent Config**: Defines GELLO device settings, joint mappings, and calibration.
- **DynamixelRobotConfig**: Motor-specific settings including IDs, offsets, signs, and gripper.
- **Control Parameters**: Update rates (`hz`), step limits (`max_steps`), and safety settings.

## Manual Configuration for Other Robots

#### Python Configuration for Non-YAM arms
- Most widely supported across different arms
- Located in `gello/agents/gello_agent.py`
- Uses `PORT_CONFIG_MAP` dictionary
- Maps USB serial ports to robot configurations

## Adding New Robots

To integrate a new robot to the Python configs:

1. **Check Compatibility**: Ensure your GELLO kinematics match the target robot
2. **Implement Robot Interface**: Create a new class implementing the `Robot` protocol from `gello/robots/robot.py`
3. **Add Configuration**: Update the configuration system with your robot's parameters

See existing implementations in `gello/robots/` for reference:
- `panda.py` - Franka Panda robot
- `ur.py` - Universal Robots
- `xarm_robot.py` - xArm robots
- `yam.py` - YAM robot

=======

#### 1. Manual `gello_agent` setup
Set your GELLO and robot arm to a known, matching configuration (see images below) and run the offset detection script.

<p align="center">
  <img src="imgs/gello_matching_joints.jpg" width="29%"/>
  <img src="imgs/robot_known_configuration.jpg" width="29%"/>
  <img src="imgs/fr3_gello_calib_pose.jpeg" width="31%"/>
</p>

**Command examples:**

**UR Robot:**
```bash
python scripts/gello_get_offset.py \
    --start-joints 0 -1.57 1.57 -1.57 -1.57 0 \
    --joint-signs 1 1 -1 1 1 1 \
    --port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FT7WBG6
```

**Franka FER (Panda):**
```bash
python scripts/gello_get_offset.py \
    --start-joints 0 0 0 -1.57 0 1.57 0 \
    --joint-signs 1 1 1 1 1 -1 1 \
    --port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FT7WBG6
```

**I2RT YAM:**
```bash
python scripts/gello_get_offset.py \
    --start-joints 0 0 0 0 0 0 \
    --joint-signs 1 -1 -1 -1 1 1 \
    --port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTAAMLV6-if00-port0
```

**Joint Signs Reference:**
- UR: `1 1 -1 1 1 1`
- Panda: `1 -1 1 1 1 -1 1`
- xArm: `1 1 1 1 1 1 1`
- YAM: `1 -1 -1 -1 1 1`

Add the generated joint offsets to `gello/agents/gello_agent.py` in the `PORT_CONFIG_MAP`.

#### 2. Create Custom YAML Configurations

1. Copy an existing config from `configs/` as a template (e.g., `yam_passive.yaml`).
2. Modify the robot `_target_` and parameters for your setup:
   - For hardware: `gello.robots.ur.URRobot`, `gello.robots.panda.PandaRobot`, etc.
   - For simulation: `gello.robots.sim_robot.MujocoRobotServer`
3. Update the agent configuration with your GELLO device settings:
   - `port`: Your U2D2 device path
   - `joint_offsets`: From the offset detection script
   - `joint_signs`: Based on your robot type
   - `start_joints`: Your GELLO's starting position

## Usage

The recommended way to launch GELLO is with a YAML configuration file.

### CAN Configuration
Robot arms such as the YAM use a CAN bus to communicate with your machine. If your arm uses a CAN bus, you will need to configure udev rules.
First, get your CAN bus ID:
```
udevadm info -a -p /sys/class/net/can* | grep -i serial
```
Then open your CAN bus rules using your text editor of choice.
```
sudo nano /etc/udev/rules.d/90-can.rules
```
If you only have one arm, add this line:
```
SUBSYSTEM=="net", ACTION=="add", ATTRS{serial}=="<your-CAN-id>", NAME="can_left"
```
If you have two arms (a bimanual setup), you will need a second line for your right arm. Your bimanual CAN rules file should contain:
```
SUBSYSTEM=="net", ACTION=="add", ATTRS{serial}=="<left-CAN-id>", NAME="can_left"
SUBSYSTEM=="net", ACTION=="add", ATTRS{serial}=="<right-CAN-id>", NAME="can_right"
```

After updating your udev rules, run the following and then unplug and reconnect your CAN devices.
```
sudo udevadm control --reload-rules && sudo systemctl restart systemd-udevd && sudo udevadm trigger
```
At this point, your CAN devices are correctly configured. If you encounter CAN connctivity issues after this point run `sh scripts/reset_all_can.sh` to reset your CAN buses.

### YAM GELLO Usage (Recommended)

First, install the YAM-specific dependency:
- **YAM**: [I2RT](https://github.com/i2rt-robotics/i2rt)
- `uv pip install -e third_party/i2rt`

**Testing in Simulation:**
Launch the simulation with the auto-generated sim config file:
```bash
python experiments/launch_yaml.py --left-config-path configs/yam_auto_generated_sim.yaml
```

**Real Robot Operation:**
Launch the real robot with the auto-generated hardware config file:
```bash
python experiments/launch_yaml.py --left-config-path configs/yam_auto_generated.yaml
```

### Launching `gello_agent` for non-YAM arms

For other robots or if not using a YAML configuration, you must launch the robot and controller nodes in separate terminals.

First, install robot-specific dependencies:
- **UR**: [ur_rtde](https://sdurobotics.gitlab.io/ur_rtde/installation/installation.html)
- **Panda**: [polymetis](https://facebookresearch.github.io/fairo/polymetis/installation.html)
- **xArm**: [xArm Python SDK](https://github.com/xArm-Developer/xArm-Python-SDK)

**1. Launch the robot node:**
```bash
# For simulation
python experiments/launch_nodes.py --robot <sim_ur|sim_panda|sim_xarm>

# For real hardware
python experiments/launch_nodes.py --robot <ur|panda|xarm>
```

**2. Launch GELLO controller:**
```bash
python experiments/run_env.py --agent=gello
```

### Troubleshooting

If, when you run `generate_yam_config.py`, you get an error detecting offsets, you may need to add your user to the dialout user group. To do so, run:
`sudo usermod -aG dialout $USER`
And then log out and log back in or restart your computer.s

If some joints in your arm are not behaving as expected, you may need to modify the joint signs of your configuration. Simply invert the affected joint sign(s) in your .yaml or `gello_agent.py` or physically reverse the installation of the servo.

### Optional: Starting Configuration

Use `--start-joints` to specify GELLO's starting configuration for automatic robot reset:
```bash
python experiments/run_env.py --agent=gello --start-joints <joint_angles>
```

## Advanced Features

### Data Collection

Collect teleoperation demonstrations with keyboard controls.

For the YAM arm launched with `launch_yaml.py`, you can append the flag `--use-save-interface` to enable data saving. This is the recommended method.

```
python experiments/launch_yaml.py --left-config-path configs/yam_passive.yaml --use-save-interface
```
After launching, you can begin saving with `s` and stop saving with `q`. Data saved will be in the `data` directory in the root of the project.

For non-YAM setups, use the following:
```bash
python experiments/run_env.py --agent=gello --use-save-interface
```
Process collected data:
```bash
python gello/data_utils/demo_to_gdict.py --source-dir=<source_dir>
```

### Bimanual Operation

The recommended way to use bimanual mode is with `launch_yaml.py`. Pass a config file for the right arm to `--right-config-path`.

```
python experiments/launch_yaml.py --left-config-path configs/gello_1.yaml --right-config-path configs/gello_2.yaml
```

For non-YAM setups, use:
```bash
python experiments/launch_nodes.py --robot=bimanual_ur
python experiments/run_env.py --agent=gello --bimanual
```
### FACTR Gravity Compensation
If you want to activate gravity compensation, all the code can be found in `gello/factr`. It works similarly to the regular launch but for now it's self-contained inside its own subdirectory and supports the YAM arm in sim and in hardware.

The YAML provides important fields that can control the strength of the gravity compensation and friction. Feel free to mess around with the strenght and friction til you attain your desired 

One important step is to add the URDF. We have provided the URDF for the active GELLO in the [Hardware Repository](https://github.com/wuphilipp/gello_mechanical). You will need to update the path in the YAML to the entry point of the URDF. 
```bash
python gello/factr/gravity_compensation.py --config configs/yam_gello_factr_hw.yaml

```

## Development

### Code Organization

```
├── scripts/             # Utility scripts
├── experiments/         # Entry points and launch scripts
├── gello/               # Core GELLO package
│   ├── agents/          # Teleoperation agents
│   ├── cameras/         # Camera interfaces
│   ├── data_utils/      # Data processing utilities
│   ├── dm_control_tasks/# MuJoCo environment utilities
│   ├── dynamixel/       # Dynamixel hardware interface
|   ├── factr/           # gravity compensation
│   ├── robots/          # Robot-specific interfaces
│   ├── utils/           # Shared launch and control utilities
│   └── zmq_core/        # ZMQ multiprocessing utilities
```

### Contributing

Install development dependencies and set up pre-commit hooks to ensure code quality before contributing:
```bash
uv pip install -r requirements_dev.txt
uv pip install pre-commit
pre-commit install
```

The codebase uses `isort` and `black` for code formatting.

We welcome contributions! Submit pull requests to help make teleoperation more accessible and higher quality.

## Citation

```bibtex
@misc{wu2023gello,
    title={GELLO: A General, Low-Cost, and Intuitive Teleoperation Framework for Robot Manipulators},
    author={Philipp Wu and Yide Shentu and Zhongke Yi and Xingyu Lin and Pieter Abbeel},
    year={2023},
}
```

## License & Acknowledgements

This project is licensed under the MIT License (see LICENSE file).

### Third-Party Dependencies
- [google-deepmind/mujoco_menagerie](https://github.com/google-deepmind/mujoco_menagerie): Robot models for MuJoCo
- [brentyi/tyro](https://github.com/brentyi/tyro): Argument parsing and configuration
- [ZMQ](https://zeromq.org/): Multiprocessing communication framework

This project uses components from ‘FACTR Teleop: Low-Cost Force-Feedback Teleoperation’ (Apache‑2.0). See `https://github.com/RaindragonD/factr_teleop/`.
