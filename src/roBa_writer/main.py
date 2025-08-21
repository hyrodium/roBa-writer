import os
import sys
import time
import shutil
import tempfile
import zipfile
import subprocess
import json
from pathlib import Path
from typing import List, Optional

import psutil
import click


class USBMonitor:
    """USB接続状況を監視するクラス (Linux専用)"""

    def __init__(self):
        self.previous_drives = self.get_usb_drives()
        self._check_udisksctl()

    def _check_udisksctl(self):
        """udisksctlの利用可能性を確認"""
        try:
            subprocess.run(["udisksctl", "help"], capture_output=True, check=True)
            click.echo("udisksctl が利用可能です")
        except (subprocess.CalledProcessError, FileNotFoundError):
            click.echo(
                "エラー: udisksctl が見つかりません。udisks2をインストールしてください。",
                err=True,
            )
            click.echo("インストール方法:")
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
            click.echo(f"未マウントデバイス検出エラー: {e}", err=True)

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
                            f"デバイスをマウントしました: {device_path} -> {mount_point}"
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
                    f"デバイスをマウントしました: {device_path} -> {mount_point}"
                )
                return mount_point

        except subprocess.CalledProcessError as e:
            click.echo(f"マウントエラー: {e}", err=True)
            click.echo("手動でデバイスをマウントしてください。", err=True)
            return None
        except Exception as e:
            click.echo(f"予期しないマウントエラー: {e}", err=True)
            return None

        click.echo("マウントポイントの取得に失敗しました", err=True)
        return None

    def wait_for_new_drive(self, timeout: int = 60) -> Optional[str]:
        """新しいUSBドライブが接続されるまで待機 (自動マウント含む)"""
        click.echo("リセットボタンをダブルクリックしてください...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            # 既存のマウント済みドライブをチェック
            current_drives = self.get_usb_drives()
            new_drives = set(current_drives) - set(self.previous_drives)

            if new_drives:
                drive = list(new_drives)[0]
                click.echo(f"USBドライブが検出されました: {drive}")
                self.previous_drives = current_drives
                return drive

            # 未マウントのUSBデバイスをチェックして自動マウント
            unmounted_devices = self.get_unmounted_usb_devices()
            for device in unmounted_devices:
                click.echo(f"未マウントのUSBデバイスを検出: {device}")
                mount_point = self.mount_device(device)
                if mount_point:
                    # マウント成功後、少し待機してからドライブリストを更新
                    time.sleep(1)
                    updated_drives = self.get_usb_drives()
                    self.previous_drives = updated_drives
                    return mount_point

            time.sleep(0.5)

        click.echo(f"タイムアウト: {timeout}秒以内にUSBドライブが検出されませんでした")
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
            click.echo(f"デバイスをアンマウントしました: {mount_point}")
            return True

        except subprocess.CalledProcessError as e:
            click.echo(f"アンマウントエラー: {e}", err=True)
            return False
        except Exception as e:
            click.echo(f"予期しないアンマウントエラー: {e}", err=True)
            return False

    def wait_for_drive_removal(self, drive_path: str, timeout: int = 30):
        """指定されたドライブが取り外されるまで待機"""
        click.echo("ファームウェア書き込み完了。USB再接続を待っています...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            if drive_path not in self.get_usb_drives():
                click.echo("USBドライブが取り外されました")
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
                f"エラー: {self.firmware_path} はディレクトリまたはZIPファイルではありません"
            )
            return None

    def _extract_zip(self) -> Optional[Path]:
        """ZIPファイルを一時ディレクトリに展開"""
        try:
            self.temp_dir = tempfile.mkdtemp(prefix="roba_writer_")
            self.firmware_dir = Path(self.temp_dir)

            with zipfile.ZipFile(self.firmware_path, "r") as zip_ref:
                zip_ref.extractall(self.firmware_dir)

            click.echo(f"ZIPファイルを展開しました: {self.firmware_dir}")
            return self.firmware_dir
        except Exception as e:
            click.echo(f"ZIPファイルの展開エラー: {e}")
            return None

    def cleanup(self):
        """一時ディレクトリのクリーンアップ"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)


class FirmwareWriter:
    """ファームウェア書き込みを行うクラス"""

    def __init__(self, firmware_dir: Path):
        self.firmware_dir = Path(firmware_dir)
        self.reset_firmware = "settings_reset-seeeduino_xiao_ble-zmk.uf2"
        self.left_firmware = "roBa_L-seeeduino_xiao_ble-zmk.uf2"
        self.right_firmware = "roBa_R-seeeduino_xiao_ble-zmk.uf2"

    def validate_firmware_files(self) -> bool:
        """ファームウェアファイルの存在を確認"""
        required_files = [self.reset_firmware, self.left_firmware, self.right_firmware]
        missing_files = []

        for file in required_files:
            if not (self.firmware_dir / file).exists():
                missing_files.append(file)

        if missing_files:
            click.echo("以下のファームウェアファイルが見つかりません:")
            for file in missing_files:
                click.echo(f"  - {file}")
            return False

        return True

    def write_firmware(self, drive_path: str, firmware_file: str) -> bool:
        """ファームウェアファイルをUSBドライブに書き込み"""
        source_path = self.firmware_dir / firmware_file
        dest_path = Path(drive_path) / firmware_file

        try:
            click.echo(f"書き込み中: {firmware_file} -> {drive_path}")
            click.echo(f"ソースパス: {source_path}")
            click.echo(f"デスティネーションパス: {dest_path}")
            click.echo(f"ソースファイル存在: {source_path.exists()}")
            click.echo(
                f"デスティネーションディレクトリ存在: {Path(drive_path).exists()}"
            )

            if not source_path.exists():
                click.echo(
                    f"エラー: ソースファイルが見つかりません: {source_path}", err=True
                )
                return False

            if not Path(drive_path).exists():
                click.echo(
                    f"エラー: デスティネーションディレクトリが見つかりません: {drive_path}",
                    err=True,
                )
                return False

            # ソースファイルの詳細情報を取得
            try:
                source_stat = source_path.stat()
                click.echo(f"ソースファイルサイズ: {source_stat.st_size} bytes")
                click.echo(f"ソースファイル権限: {oct(source_stat.st_mode)}")
            except Exception as e:
                click.echo(f"ソースファイルstat取得エラー: {e}", err=True)
                return False

            # デスティネーションの詳細情報を取得
            try:
                disk_usage = shutil.disk_usage(drive_path)
                click.echo(f"デスティネーション空き容量: {disk_usage.free} bytes")

                # マウントポイントの書き込み権限を確認
                drive_stat = Path(drive_path).stat()
                click.echo(f"デスティネーション権限: {oct(drive_stat.st_mode)}")

                # テスト用の小さなファイルを作成してみる
                test_file = Path(drive_path) / "test_write.txt"
                test_file.write_text("test")
                test_file.unlink()  # テストファイル削除
                click.echo("書き込みテスト: 成功")

            except Exception as e:
                click.echo(f"デスティネーション確認エラー: {e}", err=True)
                click.echo("デバイスが読み取り専用である可能性があります", err=True)
                return False

            # コピー直前の最終確認
            click.echo("コピー直前の最終確認...")
            click.echo(f"マウントポイント存在確認: {Path(drive_path).exists()}")
            try:
                # マウントポイント内のファイル一覧を表示
                files_in_mount = list(Path(drive_path).iterdir())
                click.echo(f"マウントポイント内のファイル数: {len(files_in_mount)}")
                for f in files_in_mount[:5]:  # 最初の5個を表示
                    click.echo(f"  - {f.name}")
            except Exception as e:
                click.echo(f"マウントポイント確認エラー: {e}")

            # UF2ファイル書き込み (書き込み完了後に自動リセットされる)
            click.echo("UF2ファイル書き込みを開始...")
            click.echo(
                "注意: UF2書き込み完了後、マイコンが自動リセットされマウントポイントが消失します"
            )

            copy_success = False

            try:
                # まずディレクトリが書き込み可能かもう一度確認
                if not os.access(drive_path, os.W_OK):
                    click.echo(
                        "警告: デスティネーションディレクトリに書き込み権限がありません"
                    )

                # UF2書き込み: ファイル作成開始
                click.echo("UF2ファイル書き込み中...")
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

                            # 進捗表示
                            progress = (written / total_size) * 100
                            click.echo(
                                f"進捗: {progress:.1f}% ({written}/{total_size} bytes)"
                            )

                            # マウントポイントが消失していないかチェック
                            if not Path(drive_path).exists():
                                click.echo(
                                    "マウントポイントが消失しました (書き込み完了)"
                                )
                                copy_success = True
                                break

                        # ファイル書き込み完了
                        click.echo("ファイル書き込み完了")
                        copy_success = True

            except (FileNotFoundError, OSError) as e:
                # UF2書き込み完了によるマウントポイント消失の可能性
                if "No such file or directory" in str(e):
                    click.echo(
                        "マウントポイントが消失しました - UF2書き込み完了の可能性があります"
                    )
                    copy_success = True
                else:
                    click.echo(f"書き込みエラー: {e}")
                    copy_success = False
            except Exception as e:
                click.echo(f"予期しない書き込みエラー: {e}")
                copy_success = False

            # 書き込み成功の判定
            if copy_success:
                click.echo("UF2ファイル書き込み成功 (マイコンがリセットされました)")
                return True
            else:
                click.echo("UF2ファイル書き込みに失敗しました", err=True)
                return False

        except PermissionError as e:
            click.echo(f"権限エラー: {e}", err=True)
            click.echo(
                "デバイスが読み取り専用でマウントされている可能性があります", err=True
            )
            return False
        except OSError as e:
            click.echo(f"OS エラー: {e}", err=True)
            click.echo("ファイルシステムやデバイスの問題の可能性があります", err=True)
            return False
        except Exception as e:
            click.echo(f"予期しない書き込みエラー: {e}", err=True)
            click.echo(f"エラータイプ: {type(e).__name__}")
            return False


class KeyboardProgrammer:
    """キーボードプログラミングのメインロジック"""

    def __init__(self, firmware_dir: Path, skip_reset: bool = False):
        self.firmware_writer = FirmwareWriter(firmware_dir)
        self.usb_monitor = USBMonitor()
        self.skip_reset = skip_reset

    def program_keyboard_side(self, side: str, firmware_file: str) -> bool:
        """一方のキーボードのプログラミングを実行"""
        click.echo(f"\n=== {side}キーボードの書き込みを開始 ===")

        # リセットファームウェアの書き込み (スキップオプションがある場合は省略)
        if not self.skip_reset:
            click.echo(
                f"{side}キーボードをUSBに接続した後にリセットボタンをダブルクリックしてください"
            )

            # リセットファームウェア書き込み
            drive = self.usb_monitor.wait_for_new_drive()
            if not drive:
                return False

            if not self.firmware_writer.write_firmware(
                drive, self.firmware_writer.reset_firmware
            ):
                return False

            # USB再接続を待機
            if not self.usb_monitor.wait_for_drive_removal(drive):
                click.echo("USB再接続の検出に失敗しました。手動で再接続してください。")

            click.echo(
                f"{side}キーボードを再度リセットボタンをダブルクリックしてください"
            )
        else:
            click.echo(
                f"{side}キーボードをUSBに接続してリセットボタンをダブルクリックしてください"
            )

        # キーボードファームウェア書き込み
        drive = self.usb_monitor.wait_for_new_drive()
        if not drive:
            return False

        if not self.firmware_writer.write_firmware(drive, firmware_file):
            return False

        # USB取り外しを待機
        self.usb_monitor.wait_for_drive_removal(drive)

        click.echo(f"{side}キーボードの書き込みが完了しました\n")
        return True

    def run(self) -> bool:
        """メインの実行ロジック"""
        if not self.firmware_writer.validate_firmware_files():
            return False

        click.echo("roBa Writer - キーボードファームウェア書き込みツール")
        click.echo(f"ファームウェアディレクトリ: {self.firmware_writer.firmware_dir}")

        if self.skip_reset:
            click.echo("リセットファームウェアの書き込みはスキップします")

        # 左キーボードの書き込み
        if not self.program_keyboard_side("左", self.firmware_writer.left_firmware):
            click.echo("左キーボードの書き込みに失敗しました", err=True)
            return False

        # 右キーボードの書き込み
        if not self.program_keyboard_side("右", self.firmware_writer.right_firmware):
            click.echo("右キーボードの書き込みに失敗しました", err=True)
            return False

        click.echo("=== 全ての書き込みが完了しました ===")
        return True


@click.command()
@click.argument("firmware_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--skip-reset",
    is_flag=True,
    help="リセットファームウェアの書き込みをスキップ (キーボードファームウェアのみ書き込み)",
)
def cli(firmware_path: Path, skip_reset: bool):
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
        programmer = KeyboardProgrammer(firmware_dir, skip_reset)
        success = programmer.run()

        sys.exit(0 if success else 1)

    finally:
        # 一時ファイルのクリーンアップ
        extractor.cleanup()


def main():
    """エントリポイント (後方互換性のため)"""
    cli()


if __name__ == "__main__":
    main()
