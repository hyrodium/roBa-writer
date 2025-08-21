import os
import sys
import time
import shutil
import tempfile
import zipfile
import subprocess
import json
import re
from pathlib import Path
from typing import List, Optional, NamedTuple
from enum import Enum

import psutil
import click


class OperationMode(Enum):
    """操作モード"""
    UPDATE_RIGHT_ONLY = "update_right_only"
    UPDATE_BOTH_WITHOUT_RESET = "update_both_without_reset"
    RESET_AND_UPDATE_BOTH = "reset_and_update_both"


class DetectedFirmware(NamedTuple):
    """検出されたファームウェアファイル情報"""
    reset_file: Optional[Path]
    left_file: Optional[Path]
    right_file: Optional[Path]


class USBMonitor:
    """USB接続状況を監視するクラス (Linux専用)"""

    def __init__(self):
        self.previous_drives = self.get_usb_drives()
        self._check_udisksctl()

    def _check_udisksctl(self):
        """udisksctlの利用可能性を確認"""
        try:
            subprocess.run(["udisksctl", "help"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            click.echo(
                "Error: udisksctl not found. Please install udisks2.",
                err=True,
            )
            click.echo("Installation instructions:")
            click.echo("  Ubuntu/Debian: sudo apt install udisks2")
            click.echo("  Arch Linux: sudo pacman -S udisks2")
            click.echo("  Fedora: sudo dnf install udisks2")
            sys.exit(1)

    def get_usb_drives(self) -> List[str]:
        """現在接続されているUSBドライブのリストを取得"""
        drives = []
        for partition in psutil.disk_partitions():
            if "removable" in partition.opts or partition.fstype in ["fat32", "vfat"]:
                if os.path.exists(partition.mountpoint):
                    drives.append(partition.mountpoint)
        return drives

    def get_unmounted_usb_devices(self) -> List[str]:
        """未マウントのUSBデバイスを検出 (Linux専用)"""
        devices = []

        try:
            # lsblkコマンドでUSBデバイスを検出
            result = subprocess.run(
                ["lsblk", "-o", "NAME,FSTYPE,MOUNTPOINT,TRAN", "-J"],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(result.stdout)

            for device in data.get("blockdevices", []):
                if device.get("tran") == "usb":
                    for child in device.get("children", [device]):
                        if child.get("fstype") in ["vfat", "fat32"] and not child.get(
                            "mountpoint"
                        ):
                            devices.append(f"/dev/{child['name']}")

        except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
            click.echo(f"Error detecting unmounted devices: {e}", err=True)

        return devices

    def mount_device(self, device_path: str) -> Optional[str]:
        """デバイスを自動マウント (udisksctl使用、sudo不要)"""
        try:
            # udisksctlでマウント
            result = subprocess.run(
                ["udisksctl", "mount", "-b", device_path],
                capture_output=True,
                text=True,
                check=True,
            )

            # マウントポイントを出力から抽出
            # "Mounted /dev/sdX1 at /media/user/DEVICE_NAME"のような出力
            for line in result.stdout.split("\n"):
                if "Mounted" in line and "at" in line:
                    mount_point = line.split(" at ")[-1].strip().rstrip(".")
                    if mount_point and os.path.exists(mount_point):
                        click.echo(
                            f"Device mounted: {device_path} -> {mount_point}"
                        )
                        return mount_point

            # 出力解析に失敗した場合、lsblkで確認
            result = subprocess.run(
                ["lsblk", "-n", "-o", "MOUNTPOINT", device_path],
                capture_output=True,
                text=True,
                check=True,
            )
            mount_point = result.stdout.strip()
            if mount_point and os.path.exists(mount_point):
                click.echo(
                    f"Device mounted: {device_path} -> {mount_point}"
                )
                return mount_point

        except subprocess.CalledProcessError as e:
            click.echo(f"Mount error: {e}", err=True)
            click.echo("Please mount the device manually.", err=True)
            return None
        except Exception as e:
            click.echo(f"Unexpected mount error: {e}", err=True)
            return None

        click.echo("Failed to get mount point", err=True)
        return None

    def wait_for_new_drive(self, timeout: int = 60) -> Optional[str]:
        """新しいUSBドライブが接続されるまで待機 (自動マウント含む)"""
        click.echo("Please double-click the reset button...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            # 既存のマウント済みドライブをチェック
            current_drives = self.get_usb_drives()
            new_drives = set(current_drives) - set(self.previous_drives)

            if new_drives:
                drive = list(new_drives)[0]
                click.echo(f"USB drive detected: {drive}")
                self.previous_drives = current_drives
                return drive

            # 未マウントのUSBデバイスをチェックして自動マウント
            unmounted_devices = self.get_unmounted_usb_devices()
            for device in unmounted_devices:
                click.echo(f"Detected unmounted USB device: {device}")
                mount_point = self.mount_device(device)
                if mount_point:
                    # マウント成功後、少し待機してからドライブリストを更新
                    time.sleep(1)
                    updated_drives = self.get_usb_drives()
                    self.previous_drives = updated_drives
                    return mount_point

            time.sleep(0.5)

        click.echo(f"Timeout: USB drive not detected within {timeout} seconds")
        return None

    def unmount_device(self, mount_point: str) -> bool:
        """デバイスをアンマウント (udisksctl使用、sudo不要)"""
        try:
            # udisksctlでアンマウント
            subprocess.run(
                ["udisksctl", "unmount", "-p", mount_point],
                check=True,
                capture_output=True,
            )
            click.echo(f"Device unmounted: {mount_point}")
            return True

        except subprocess.CalledProcessError as e:
            click.echo(f"Unmount error: {e}", err=True)
            return False
        except Exception as e:
            click.echo(f"Unexpected unmount error: {e}", err=True)
            return False

    def wait_for_drive_removal(self, drive_path: str, timeout: int = 30):
        """指定されたドライブが取り外されるまで待機"""
        click.echo("Firmware writing completed. Waiting for USB reconnection...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            if drive_path not in self.get_usb_drives():
                click.echo("USB drive removed")
                time.sleep(1)  # 少し待機
                self.previous_drives = self.get_usb_drives()
                return True
            time.sleep(0.5)

        return False


class FirmwareExtractor:
    """ファームウェアファイルの抽出とディレクトリ準備を行うクラス"""

    def __init__(self, firmware_path: Path):
        self.firmware_path = Path(firmware_path)
        self.temp_dir = None
        self.firmware_dir = None

    def prepare_firmware_dir(self) -> Optional[Path]:
        """ZIPファイルまたはディレクトリからファームウェアディレクトリを準備"""
        if self.firmware_path.is_file() and self.firmware_path.suffix.lower() == ".zip":
            # ZIPファイルの場合は展開
            return self._extract_zip()
        elif self.firmware_path.is_dir():
            # ディレクトリの場合はそのまま使用
            self.firmware_dir = self.firmware_path
            return self.firmware_dir
        else:
            click.echo(
                f"Error: {self.firmware_path} is not a directory or ZIP file"
            )
            return None

    def _extract_zip(self) -> Optional[Path]:
        """ZIPファイルを一時ディレクトリに展開"""
        try:
            self.temp_dir = tempfile.mkdtemp(prefix="roba_writer_")
            self.firmware_dir = Path(self.temp_dir)

            with zipfile.ZipFile(self.firmware_path, "r") as zip_ref:
                zip_ref.extractall(self.firmware_dir)

            return self.firmware_dir
        except Exception as e:
            click.echo(f"ZIP file extraction error: {e}")
            return None

    def cleanup(self):
        """一時ディレクトリのクリーンアップ"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)


class FirmwareWriter:
    """ファームウェア書き込みを行うクラス"""

    def __init__(self, firmware_dir: Path):
        self.firmware_dir = Path(firmware_dir)
        self.detected_firmware = self._detect_firmware_files()

    def _detect_firmware_files(self) -> DetectedFirmware:
        """ファイル名パターンからファームウェアファイルを自動検出"""
        uf2_files = list(self.firmware_dir.glob("*.uf2"))
        
        reset_file = None
        left_file = None
        right_file = None
        
        # パターンマッチング用の正規表現
        reset_patterns = [
            r'.*reset.*',  # "reset" を含む
        ]
        
        left_patterns = [
            r'.*_L.*',  # "_L" を含む
            r'.*-L.*',  # "-L" を含む  
        ]
        
        right_patterns = [
            r'.*_R.*',  # "_R" を含む
            r'.*-R.*',  # "-R" を含む
        ]
        
        for file_path in uf2_files:
            filename = file_path.name  # 大文字小文字を保持
            
            # リセットファイルの検出
            if not reset_file and any(re.match(pattern, filename, re.IGNORECASE) for pattern in reset_patterns):
                reset_file = file_path
                continue
            
            # より具体的な "left" / "right" を優先してチェック
            # 左手用ファイルの検出
            if not left_file and any(re.match(pattern, filename, re.IGNORECASE) for pattern in left_patterns):
                left_file = file_path
                continue
                
            # 右手用ファイルの検出  
            if not right_file and any(re.match(pattern, filename, re.IGNORECASE) for pattern in right_patterns):
                right_file = file_path
                continue
        
        return DetectedFirmware(reset_file, left_file, right_file)

    def display_firmware_files(self) -> bool:
        """ファームウェアファイルの一覧表示と検出結果"""
        click.echo("\n=== Firmware Files ===")
        
        all_files = list(self.firmware_dir.glob("*.uf2"))
        if not all_files:
            click.echo("No .uf2 files found in the firmware directory")
            return False
        
        def get_file_purpose(file_path: Path) -> str:
            """ファイルの用途を判定"""
            if file_path == self.detected_firmware.reset_file:
                return "Reset firmware"
            elif file_path == self.detected_firmware.left_file:
                return "Left keyboard"
            elif file_path == self.detected_firmware.right_file:
                return "Right keyboard"
            else:
                return "Unknown"
        
        click.echo("Found .uf2 files:")
        for file in sorted(all_files):
            file_size = file.stat().st_size
            purpose = get_file_purpose(file)
            if purpose == "Unknown":
                click.echo(f"  ✗ {file.name} ({file_size} bytes) - {purpose}")
            else:
                click.echo(f"  ✓ {file.name} ({file_size} bytes) - {purpose}")

        # 必要なファイルが見つからない場合の警告
        missing_files = []
        if self.detected_firmware.reset_file is None:
            missing_files.append("Reset firmware")
        if self.detected_firmware.left_file is None:
            missing_files.append("Left keyboard")
        if self.detected_firmware.right_file is None:
            missing_files.append("Right keyboard")
        
        if missing_files:
            click.echo(f"\nWarning: {len(missing_files)} required file(s) not detected: {', '.join(missing_files)}")
            click.echo("Please ensure firmware files contain these patterns:")
            click.echo("  - Reset firmware: 'reset'")
            click.echo("  - Left keyboard: '_L' or '-L'")
            click.echo("  - Right keyboard: '_R' or '-R'")
            
        return len(missing_files) == 0

    def write_firmware(self, drive_path: str, firmware_file: Path) -> bool:
        """ファームウェアファイルをUSBドライブに書き込み"""
        source_path = firmware_file
        dest_path = Path(drive_path) / firmware_file.name

        try:
            click.echo(f"Writing: {firmware_file.name} -> {drive_path}")
            click.echo(f"Source path: {source_path}")
            click.echo(f"Destination path: {dest_path}")
            click.echo(f"Source file exists: {source_path.exists()}")
            click.echo(
                f"Destination directory exists: {Path(drive_path).exists()}"
            )

            if not source_path.exists():
                click.echo(
                    f"Error: Source file not found: {source_path}", err=True
                )
                return False

            if not Path(drive_path).exists():
                click.echo(
                    f"Error: Destination directory not found: {drive_path}",
                    err=True,
                )
                return False

            # ソースファイルの詳細情報を取得
            try:
                source_stat = source_path.stat()
                click.echo(f"Source file size: {source_stat.st_size} bytes")
                click.echo(f"Source file permissions: {oct(source_stat.st_mode)}")
            except Exception as e:
                click.echo(f"Error getting source file stats: {e}", err=True)
                return False

            # デスティネーションの詳細情報を取得
            try:
                disk_usage = shutil.disk_usage(drive_path)
                click.echo(f"Destination free space: {disk_usage.free} bytes")

                # マウントポイントの書き込み権限を確認
                drive_stat = Path(drive_path).stat()
                click.echo(f"Destination permissions: {oct(drive_stat.st_mode)}")

                # テスト用の小さなファイルを作成してみる
                test_file = Path(drive_path) / "test_write.txt"
                test_file.write_text("test")
                test_file.unlink()  # テストファイル削除
                click.echo("Write test: successful")

            except Exception as e:
                click.echo(f"Destination check error: {e}", err=True)
                click.echo("Device may be mounted read-only", err=True)
                return False

            # コピー直前の最終確認
            click.echo("Final check before copy...")
            click.echo(f"Mount point exists: {Path(drive_path).exists()}")
            try:
                # マウントポイント内のファイル一覧を表示
                files_in_mount = list(Path(drive_path).iterdir())
                click.echo(f"Files in mount point: {len(files_in_mount)}")
                for f in files_in_mount[:5]:  # 最初の5個を表示
                    click.echo(f"  - {f.name}")
            except Exception as e:
                click.echo(f"Mount point check error: {e}")

            # UF2ファイル書き込み (書き込み完了後に自動リセットされる)
            click.echo(f"Starting UF2 file write: {firmware_file.name}")
            click.echo(
                "Note: After UF2 write completes, the microcontroller will auto-reset and mount point will disappear"
            )

            copy_success = False

            try:
                # まずディレクトリが書き込み可能かもう一度確認
                if not os.access(drive_path, os.W_OK):
                    click.echo(
                        "Warning: No write permission for destination directory"
                    )

                # UF2書き込み: ファイル作成開始
                click.echo("Writing UF2 file...")
                with open(source_path, "rb") as src:
                    with open(dest_path, "wb") as dst:
                        # チャンクサイズを小さくして進捗を確認
                        chunk_size = 8192
                        total_size = source_path.stat().st_size
                        written = 0

                        while True:
                            chunk = src.read(chunk_size)
                            if not chunk:
                                break
                            dst.write(chunk)
                            written += len(chunk)

                            # 進捗表示 (同一行を更新)
                            progress = (written / total_size) * 100
                            print(f"\rProgress: {progress:.1f}% ({written}/{total_size} bytes)", end="", flush=True)

                            # マウントポイントが消失していないかチェック
                            if not Path(drive_path).exists():
                                print()  # 進捗行の後に改行
                                click.echo(
                                    "Mount point disappeared (write completed)"
                                )
                                copy_success = True
                                break

                        # ファイル書き込み完了
                        print()  # 進捗行の後に改行
                        click.echo("File write completed")
                        copy_success = True

            except (FileNotFoundError, OSError) as e:
                # UF2書き込み完了によるマウントポイント消失の可能性
                if "No such file or directory" in str(e):
                    print()  # 進捗行の後に改行
                    click.echo(
                        "Mount point disappeared - UF2 write likely completed successfully"
                    )
                    copy_success = True
                else:
                    click.echo(f"Write error: {e}")
                    copy_success = False
            except Exception as e:
                click.echo(f"Unexpected write error: {e}")
                copy_success = False

            # 書き込み成功の判定
            if copy_success:
                click.echo("UF2 file write successful (microcontroller reset)")
                return True
            else:
                click.echo("UF2 file write failed", err=True)
                return False

        except PermissionError as e:
            click.echo(f"Permission error: {e}", err=True)
            click.echo(
                "Device may be mounted read-only", err=True
            )
            return False
        except OSError as e:
            click.echo(f"OS error: {e}", err=True)
            click.echo("Possible filesystem or device issue", err=True)
            return False
        except Exception as e:
            click.echo(f"Unexpected write error: {e}", err=True)
            click.echo(f"Error type: {type(e).__name__}")
            return False


class KeyboardProgrammer:
    """キーボードプログラミングのメインロジック"""

    def __init__(self, firmware_dir: Path):
        self.firmware_writer = FirmwareWriter(firmware_dir)
        self.usb_monitor = USBMonitor()

    def get_operation_mode(self) -> Optional[OperationMode]:
        """インタラクティブに操作モードを選択"""
        click.echo("\n=== Operation Mode Selection ===")
        click.echo("Please select an operation mode:")
        click.echo("1. Update right (main) device only (without reset)")
        click.echo("2. Update both devices (without reset)")  
        click.echo("3. Reset and update both devices")
        click.echo("4. Exit")
        
        while True:
            try:
                choice = click.prompt("Enter your choice (1-4)", type=int)
                if choice == 1:
                    return OperationMode.UPDATE_RIGHT_ONLY
                elif choice == 2:
                    return OperationMode.UPDATE_BOTH_WITHOUT_RESET
                elif choice == 3:
                    return OperationMode.RESET_AND_UPDATE_BOTH
                elif choice == 4:
                    click.echo("Exiting...")
                    return None
                else:
                    click.echo("Invalid choice. Please enter 1-4.")
            except (click.ClickException, ValueError):
                click.echo("Invalid input. Please enter a number between 1-4.")

    def program_keyboard_side(self, side: str, firmware_file: Optional[Path], with_reset: bool = False) -> bool:
        """一方のキーボードのプログラミングを実行"""
        if firmware_file is None:
            click.echo(f"Error: {side} keyboard firmware file not found", err=True)
            return False
            
        click.echo(f"\n=== Starting {side} keyboard firmware write ===")

        # リセットファームウェアの書き込み (with_resetがTrueの場合)
        if with_reset:
            click.echo(
                f"Connect {side} keyboard via USB, then double-click the reset button"
            )

            # リセットファームウェア書き込み
            drive = self.usb_monitor.wait_for_new_drive()
            if not drive:
                return False

            reset_file = self.firmware_writer.detected_firmware.reset_file
            if reset_file is None:
                click.echo("Error: Reset firmware file not found", err=True)
                return False
                
            if not self.firmware_writer.write_firmware(drive, reset_file):
                return False

            # USB再接続を待機
            if not self.usb_monitor.wait_for_drive_removal(drive):
                click.echo("Failed to detect USB reconnection. Please reconnect manually.")

            click.echo(
                f"Double-click the reset button on {side} keyboard again"
            )
        else:
            click.echo(
                f"Connect {side} keyboard via USB and double-click the reset button"
            )

        # キーボードファームウェア書き込み
        drive = self.usb_monitor.wait_for_new_drive()
        if not drive:
            return False

        if not self.firmware_writer.write_firmware(drive, firmware_file):
            return False

        # USB取り外しを待機
        self.usb_monitor.wait_for_drive_removal(drive)

        click.echo(f"{side} keyboard firmware write completed\n")
        return True

    def run(self) -> bool:
        """メインの実行ロジック"""
        click.echo("roBa Writer - Keyboard Firmware Writing Tool")
        click.echo(f"Firmware directory: {self.firmware_writer.firmware_dir}")

        # ファームウェアファイル一覧表示
        if not self.firmware_writer.display_firmware_files():
            return False

        # 操作モード選択
        operation_mode = self.get_operation_mode()
        if operation_mode is None:
            return False

        # 選択されたモードに応じて処理実行
        success = True
        detected = self.firmware_writer.detected_firmware
        
        if operation_mode == OperationMode.UPDATE_RIGHT_ONLY:
            click.echo("\n=== Updating right (main) keyboard only ===")
            success = self.program_keyboard_side("right", detected.right_file, with_reset=False)
            
        elif operation_mode == OperationMode.UPDATE_BOTH_WITHOUT_RESET:
            click.echo("\n=== Updating both keyboards without reset ===")
            # 左キーボードの書き込み
            if not self.program_keyboard_side("left", detected.left_file, with_reset=False):
                click.echo("Left keyboard firmware write failed", err=True)
                success = False
            elif not self.program_keyboard_side("right", detected.right_file, with_reset=False):
                click.echo("Right keyboard firmware write failed", err=True)
                success = False
                
        elif operation_mode == OperationMode.RESET_AND_UPDATE_BOTH:
            click.echo("\n=== Reset and update both keyboards ===")
            # 左キーボードの書き込み (リセット付き)
            if not self.program_keyboard_side("left", detected.left_file, with_reset=True):
                click.echo("Left keyboard firmware write failed", err=True)
                success = False
            elif not self.program_keyboard_side("right", detected.right_file, with_reset=True):
                click.echo("Right keyboard firmware write failed", err=True)
                success = False

        if success:
            click.echo("=== All firmware writes completed successfully ===")
        else:
            click.echo("=== Firmware write process failed ===", err=True)
            
        return success


@click.command()
@click.argument("firmware_path", type=click.Path(exists=True, path_type=Path))
def cli(firmware_path: Path):
    """roBa Writer - Seeed xiao nrf52840 左右分割式キーボードファームウェア書き込みツール

    FIRMWARE_PATH: ファームウェアファイル(.uf2)が含まれるディレクトリまたはZIPファイルのパス
    """
    extractor = FirmwareExtractor(firmware_path)

    try:
        # ファームウェアディレクトリの準備
        firmware_dir = extractor.prepare_firmware_dir()
        if not firmware_dir:
            sys.exit(1)

        # キーボードプログラミングを実行
        programmer = KeyboardProgrammer(firmware_dir)
        success = programmer.run()

        sys.exit(0 if success else 1)

    finally:
        # 一時ファイルのクリーンアップ
        extractor.cleanup()
