#!/usr/bin/env python3
"""
Hermetic Native Package Builder for DMD Media Showcase:
- macOS (`arm64` / `x64`): Native `.dmg` installer (`DMD_Media_Showcase_macOS_<arch>.dmg`) containing
  `DMD Media Showcase.app` (native Cocoa Menu-Bar controller + PyInstaller server + bundled FFmpeg dylibs
  + `Applications` drag-and-drop shortcut, ad-hoc or Developer ID signed & optionally notarized).
- Windows (`x64`): Self-contained `.zip` (`DMD_Media_Showcase_Windows_x64.zip`) with ONLY Windows files
  (`dmd-media-showcase.exe`, `Start_Showcase_Windows.bat`, `_internal/bin/ffmpeg.exe`).
- Linux (`x64`): Self-contained `.tar.gz` (`DMD_Media_Showcase_Linux_x64.tar.gz`) preserving Unix `+x`
  permissions (`dmd-media-showcase`, `start_showcase.sh`, `_internal/bin/ffmpeg`).
"""

from __future__ import annotations

import os
import platform
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"

MACOS_LAUNCHER_OBJC = r"""
#import <Cocoa/Cocoa.h>
#include <signal.h>
#include <unistd.h>

@interface DMDAppDelegate : NSObject <NSApplicationDelegate>
@property (strong, nonatomic) NSStatusItem *statusItem;
@property (strong, nonatomic) NSMenuItem *stateMenuItem;
@property (strong, nonatomic) NSTask *serverTask;
@property (strong, nonatomic) NSTimer *healthTimer;
@property (assign, nonatomic) BOOL browserOpened;
@property (assign, nonatomic) BOOL shuttingDown;
@property (assign, nonatomic) NSInteger healthAttempts;
@end

@implementation DMDAppDelegate

- (NSString *)logFilePath {
    NSString *logsDir = [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Logs"];
    [[NSFileManager defaultManager] createDirectoryAtPath:logsDir
                              withIntermediateDirectories:YES
                                               attributes:nil
                                                    error:nil];
    return [logsDir stringByAppendingPathComponent:@"DMDMediaShowcase.log"];
}

- (NSString *)dataDirPath {
    NSString *dataDir = [NSHomeDirectory()
        stringByAppendingPathComponent:@"Library/Application Support/DMDMediaShowcase/data"];
    [[NSFileManager defaultManager] createDirectoryAtPath:dataDir
                              withIntermediateDirectories:YES
                                               attributes:nil
                                                    error:nil];
    return dataDir;
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    NSString *bundleId = [[NSBundle mainBundle] bundleIdentifier];
    if (bundleId.length > 0) {
        NSArray<NSRunningApplication *> *running =
            [NSRunningApplication runningApplicationsWithBundleIdentifier:bundleId];
        if (running.count > 1) {
            [[NSWorkspace sharedWorkspace] openURL:[NSURL URLWithString:@"http://localhost:8890/"]];
            [NSApp terminate:nil];
            return;
        }
    }

    self.statusItem = [[NSStatusBar systemStatusBar] statusItemWithLength:NSVariableStatusItemLength];
    self.statusItem.button.title = @"▣ DMD";
    self.statusItem.button.toolTip = @"DMD Media Showcase Server (:8890 / MQTT :1883)";

    NSMenu *menu = [[NSMenu alloc] initWithTitle:@"DMD Media Showcase"];
    self.stateMenuItem = [[NSMenuItem alloc] initWithTitle:@"⏳ Starting Showcase Server (:8890)…"
                                                    action:nil
                                             keyEquivalent:@""];
    self.stateMenuItem.enabled = NO;
    [menu addItem:self.stateMenuItem];
    [menu addItem:[NSMenuItem separatorItem]];

    NSMenuItem *openItem = [[NSMenuItem alloc] initWithTitle:@"Open Web Dashboard (localhost:8890)"
                                                      action:@selector(openDashboard:)
                                               keyEquivalent:@"o"];
    openItem.target = self;
    [menu addItem:openItem];

    NSMenuItem *dataItem = [[NSMenuItem alloc] initWithTitle:@"Open Data Folder"
                                                      action:@selector(openDataFolder:)
                                               keyEquivalent:@""];
    dataItem.target = self;
    [menu addItem:dataItem];

    NSMenuItem *logsItem = [[NSMenuItem alloc] initWithTitle:@"View Server Logs"
                                                      action:@selector(openLogs:)
                                               keyEquivalent:@""];
    logsItem.target = self;
    [menu addItem:logsItem];

    [menu addItem:[NSMenuItem separatorItem]];

    NSMenuItem *quitItem = [[NSMenuItem alloc] initWithTitle:@"Quit DMD Media Showcase"
                                                      action:@selector(quitApp:)
                                               keyEquivalent:@"q"];
    quitItem.target = self;
    [menu addItem:quitItem];

    self.statusItem.menu = menu;

    [self startServerProcess];
    self.healthTimer = [NSTimer scheduledTimerWithTimeInterval:0.6
                                                        target:self
                                                      selector:@selector(pollHealth:)
                                                      userInfo:nil
                                                       repeats:YES];
}

- (void)startServerProcess {
    NSString *macOSDir = [[[NSBundle mainBundle] executablePath] stringByDeletingLastPathComponent];
    NSString *serverBin = [macOSDir stringByAppendingPathComponent:@"dmd-media-showcase-server"];
    NSString *logPath = [self logFilePath];
    [[NSFileManager defaultManager] createFileAtPath:logPath contents:nil attributes:nil];
    NSFileHandle *logHandle = [NSFileHandle fileHandleForWritingAtPath:logPath];

    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:serverBin];
    NSMutableDictionary *env = [[[NSProcessInfo processInfo] environment] mutableCopy];
    env[@"DMD_PORTABLE_MODE"] = @"1";
    env[@"DMD_DATA_DIR"] = [self dataDirPath];
    task.environment = env;
    if (logHandle != nil) {
        task.standardOutput = logHandle;
        task.standardError = logHandle;
    }

    __weak typeof(self) weakSelf = self;
    task.terminationHandler = ^(NSTask *finishedTask) {
        dispatch_async(dispatch_get_main_queue(), ^{
            __strong typeof(weakSelf) strongSelf = weakSelf;
            if (!strongSelf || strongSelf.shuttingDown) {
                return;
            }
            strongSelf.stateMenuItem.title =
                [NSString stringWithFormat:@"🔴 Server stopped (code %d)", finishedTask.terminationStatus];
        });
    };

    NSError *launchErr = nil;
    if (![task launchAndReturnError:&launchErr]) {
        self.stateMenuItem.title = @"🔴 Failed to start server (see Logs)";
        return;
    }
    self.serverTask = task;
}

- (void)pollHealth:(NSTimer *)timer {
    self.healthAttempts += 1;
    NSURL *url = [NSURL URLWithString:@"http://127.0.0.1:8890/api/v1/health"];
    NSURLSessionConfiguration *cfg = [NSURLSessionConfiguration ephemeralSessionConfiguration];
    cfg.timeoutIntervalForRequest = 0.5;
    NSURLSession *session = [NSURLSession sessionWithConfiguration:cfg];

    __weak typeof(self) weakSelf = self;
    [[session dataTaskWithURL:url
            completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
                NSHTTPURLResponse *http = (NSHTTPURLResponse *)response;
                if (error == nil && [http isKindOfClass:[NSHTTPURLResponse class]] && http.statusCode == 200) {
                    dispatch_async(dispatch_get_main_queue(), ^{
                        __strong typeof(weakSelf) strongSelf = weakSelf;
                        if (!strongSelf) {
                            return;
                        }
                        strongSelf.stateMenuItem.title = @"🟢 Running on localhost:8890 (MQTT :1883)";
                        [strongSelf.healthTimer invalidate];
                        strongSelf.healthTimer = nil;
                        if (!strongSelf.browserOpened) {
                            strongSelf.browserOpened = YES;
                            [[NSWorkspace sharedWorkspace]
                                openURL:[NSURL URLWithString:@"http://localhost:8890/"]];
                        }
                    });
                }
            }] resume];

    if (self.healthAttempts > 60) {
        [self.healthTimer invalidate];
        self.healthTimer = nil;
    }
}

- (BOOL)applicationShouldHandleReopen:(NSApplication *)sender hasVisibleWindows:(BOOL)flag {
    [self openDashboard:nil];
    return NO;
}

- (void)openDashboard:(id)sender {
    [[NSWorkspace sharedWorkspace] openURL:[NSURL URLWithString:@"http://localhost:8890/"]];
}

- (void)openDataFolder:(id)sender {
    [[NSWorkspace sharedWorkspace] openURL:[NSURL fileURLWithPath:[self dataDirPath]]];
}

- (void)openLogs:(id)sender {
    [[NSWorkspace sharedWorkspace] openURL:[NSURL fileURLWithPath:[self logFilePath]]];
}

- (void)quitApp:(id)sender {
    [NSApp terminate:nil];
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    self.shuttingDown = YES;
    [self.healthTimer invalidate];
    if (self.serverTask != nil && self.serverTask.isRunning) {
        pid_t pid = self.serverTask.processIdentifier;
        [self.serverTask terminate];
        for (int i = 0; i < 25 && self.serverTask.isRunning; ++i) {
            usleep(100000);
        }
        if (self.serverTask.isRunning && pid > 0) {
            kill(pid, SIGKILL);
        }
    }
}

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *app = [NSApplication sharedApplication];
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        DMDAppDelegate *delegate = [[DMDAppDelegate alloc] init];
        app.delegate = delegate;
        [app run];
    }
    return 0;
}
"""


def _read_app_version() -> str:
    init_file = ROOT / "app" / "__init__.py"
    if init_file.exists():
        for line in init_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                parts = line.split('"')
                if len(parts) >= 2:
                    return parts[1].strip()
    return "1.0.0"


def _detect_os_arch_tag() -> str:
    sys_name = platform.system().lower()
    machine = platform.machine().lower()
    if "windows" in sys_name or sys.platform == "win32":
        os_tag = "Windows"
    elif "darwin" in sys_name or sys.platform == "darwin":
        os_tag = "macOS"
    else:
        os_tag = "Linux"

    if machine in ("amd64", "x86_64", "x64"):
        arch_tag = "x64"
    elif machine in ("arm64", "aarch64"):
        arch_tag = "arm64"
    else:
        arch_tag = machine or "x64"
    return f"{os_tag}_{arch_tag}"


def _generate_macos_icns(dest_icns: Path) -> Optional[Path]:
    """Generate a crisp HUB75 LED-matrix macOS `.icns` icon via Pillow + `/usr/bin/iconutil`."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None

    iconutil = shutil.which("iconutil")
    if not iconutil:
        return None

    size = 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded macOS squircle background
    pad = 64
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=210,
        fill=(14, 16, 28, 255),
        outline=(56, 189, 248, 255),
        width=14,
    )

    # Retro HUB75 12x6 glowing LED dot matrix motif
    cols, rows = 12, 6
    grid_w, grid_h = 720, 380
    x0 = (size - grid_w) // 2
    y0 = (size - grid_h) // 2
    cell_w = grid_w / cols
    cell_h = grid_h / rows
    dot_r = int(min(cell_w, cell_h) * 0.34)

    palette = [
        (255, 168, 0, 255),    # Arcade Amber
        (56, 189, 248, 255),   # Cyber Cyan
        (244, 63, 94, 255),    # Neon Rose
        (168, 85, 247, 255),   # Synthwave Violet
    ]
    # Stylized "PLAY ▶" triangle on the LED grid
    play_lit = {
        (4, 1), (4, 2), (4, 3), (4, 4),
        (5, 1), (5, 2), (5, 3), (5, 4),
        (6, 2), (6, 3),
        (7, 2), (7, 3),
    }
    for r in range(rows):
        for c in range(cols):
            cx = int(x0 + (c + 0.5) * cell_w)
            cy = int(y0 + (r + 0.5) * cell_h)
            if (c, r) in play_lit:
                color = (255, 196, 32, 255)
            elif r in (0, rows - 1) or c in (0, cols - 1):
                color = palette[(c + r) % len(palette)]
            else:
                color = (34, 40, 62, 255)
            draw.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=color)

    with tempfile.TemporaryDirectory(prefix="dmd_icon_") as tmp:
        iconset_dir = Path(tmp) / "AppIcon.iconset"
        iconset_dir.mkdir(parents=True, exist_ok=True)
        specs = [
            ("icon_16x16.png", 16),
            ("icon_16x16@2x.png", 32),
            ("icon_32x32.png", 32),
            ("icon_32x32@2x.png", 64),
            ("icon_128x128.png", 128),
            ("icon_128x128@2x.png", 256),
            ("icon_256x256.png", 256),
            ("icon_256x256@2x.png", 512),
            ("icon_512x512.png", 512),
            ("icon_512x512@2x.png", 1024),
        ]
        for fname, px in specs:
            resized = img.resize((px, px), Image.Resampling.LANCZOS)
            resized.save(iconset_dir / fname, format="PNG")

        dest_icns.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run([iconutil, "-c", "icns", str(iconset_dir), "-o", str(dest_icns)], capture_output=True, text=True)
        if res.returncode == 0 and dest_icns.exists():
            return dest_icns
    return None


def _compile_macos_menubar_launcher(output_bin: Path) -> None:
    """Compile the native Objective-C Cocoa Menu-Bar launcher into `Contents/MacOS/DMD Media Showcase`."""
    clang = shutil.which("clang")
    if not clang:
        raise RuntimeError("macOS clang compiler not found in PATH")

    arch_flag = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    with tempfile.TemporaryDirectory(prefix="dmd_launcher_") as tmp:
        src_m = Path(tmp) / "launcher.m"
        src_m.write_text(MACOS_LAUNCHER_OBJC, encoding="utf-8")
        cmd = [
            clang,
            "-O2",
            "-Wall",
            "-fobjc-arc",
            "-arch",
            arch_flag,
            "-mmacosx-version-min=12.0",
            "-framework",
            "Cocoa",
            str(src_m),
            "-o",
            str(output_bin),
        ]
        subprocess.run(cmd, check=True)
    output_bin.chmod(0o755)


def _sign_and_optionally_notarize_macos(app_bundle: Path, dmg_path: Path) -> None:
    """
    Sign `DMD Media Showcase.app` and the `.dmg` disk image:
    - Uses Developer ID Application + Hardened Runtime + Apple Notarization when CI secrets are set.
    - Otherwise applies a clean deep ad-hoc signature (`codesign --force --deep --sign -`),
      required on Apple Silicon (`arm64`) so Mach-O binaries pass kernel signature verification.
    """
    signing_identity = (os.environ.get("APPLE_SIGNING_IDENTITY") or "").strip() or "-"
    sign_cmd_app = ["codesign", "--force", "--deep"]
    if signing_identity != "-":
        sign_cmd_app += ["--options", "runtime", "--timestamp"]
    sign_cmd_app += ["--sign", signing_identity, str(app_bundle)]

    print(f"[MACOS] Signing {app_bundle.name} (identity={'Developer ID' if signing_identity != '-' else 'ad-hoc'}) ...")
    subprocess.run(sign_cmd_app, check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app_bundle)], check=True)

    # Build compressed UDZO .dmg via hdiutil
    if dmg_path.exists():
        dmg_path.unlink()
    subprocess.run(
        [
            "hdiutil",
            "create",
            "-volname",
            "DMD Media Showcase",
            "-fs",
            "HFS+",
            "-srcfolder",
            str(app_bundle.parent),
            "-ov",
            "-format",
            "UDZO",
            str(dmg_path),
        ],
        check=True,
    )
    subprocess.run(["codesign", "--force", "--sign", signing_identity, str(dmg_path)], check=True)

    apple_id = (os.environ.get("APPLE_ID") or "").strip()
    team_id = (os.environ.get("APPLE_TEAM_ID") or "").strip()
    app_pw = (os.environ.get("APPLE_APP_SPECIFIC_PASSWORD") or "").strip()
    if signing_identity != "-" and apple_id and team_id and app_pw:
        print(f"[MACOS] Submitting {dmg_path.name} to Apple Notary Service ...")
        subprocess.run(
            [
                "xcrun",
                "notarytool",
                "submit",
                str(dmg_path),
                "--apple-id",
                apple_id,
                "--team-id",
                team_id,
                "--password",
                app_pw,
                "--wait",
            ],
            check=True,
        )
        subprocess.run(["xcrun", "stapler", "staple", str(dmg_path)], check=True)
        print(f"[MACOS] Apple Notarization & Stapling completed for {dmg_path.name}.")


def _base_pyinstaller_args(name: str) -> List[str]:
    sep = ";" if sys.platform == "win32" else ":"
    static_src = ROOT / "app" / "infrastructure" / "web" / "static"
    asyncapi_src = ROOT / "docs" / "asyncapi.yaml"
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        name,
        "--add-data",
        f"{static_src}{sep}app/infrastructure/web/static",
        "--add-data",
        f"{asyncapi_src}{sep}docs",
        "--collect-submodules",
        "app",
        "--collect-submodules",
        "dmd_gif_converter",
        "--collect-submodules",
        "src.engine",
        "--collect-submodules",
        "src.plugins",
        "--exclude-module",
        "src.ui",
        "--exclude-module",
        "tkinter",
        "--exclude-module",
        "customtkinter",
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "yt_dlp",
    ]


def _build_macos_dmg(tag: str) -> Path:
    app_ver = _read_app_version()
    icns_path = BUILD_DIR / "AppIcon.icns"
    built_icns = _generate_macos_icns(icns_path)

    pyi_cmd = _base_pyinstaller_args("dmd-media-showcase-server") + [
        "--windowed",
        "--osx-bundle-identifier",
        "com.arcadematrix.dmdmediashowcase",
    ]
    if built_icns:
        pyi_cmd += ["--icon", str(built_icns)]

    # Use PyInstaller `--add-binary` so Homebrew FFmpeg/FFprobe `.dylib` dependencies are
    # automatically collected into `Contents/Frameworks/` and rewritten to `@loader_path`!
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        if found:
            pyi_cmd += ["--add-binary", f"{Path(found).resolve()}:bin"]

    pyi_cmd.append(str(ROOT / "app" / "main.py"))
    print(f"[MACOS] Running PyInstaller .app bundle build ({tag}, v{app_ver}) ...")
    subprocess.run(pyi_cmd, cwd=str(ROOT), check=True)

    raw_app = DIST_DIR / "dmd-media-showcase-server.app"
    staging_dir = DIST_DIR / "dmg_staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    final_app = staging_dir / "DMD Media Showcase.app"
    raw_app.rename(final_app)

    # Remove intermediate PyInstaller folder `dist/dmd-media-showcase-server`
    raw_onedir = DIST_DIR / "dmd-media-showcase-server"
    if raw_onedir.exists():
        shutil.rmtree(raw_onedir)

    # Compile native Cocoa Menu-Bar & Dock Cmd+Q controller into Contents/MacOS/DMD Media Showcase
    launcher_bin = final_app / "Contents" / "MacOS" / "DMD Media Showcase"
    _compile_macos_menubar_launcher(launcher_bin)

    # Update Info.plist so LaunchServices runs the native Menu-Bar controller
    plist_path = final_app / "Contents" / "Info.plist"
    with plist_path.open("rb") as fp:
        plist_data = plistlib.load(fp)
    plist_data["CFBundleExecutable"] = "DMD Media Showcase"
    plist_data["CFBundleName"] = "DMD Media Showcase"
    plist_data["CFBundleDisplayName"] = "DMD Media Showcase"
    plist_data["CFBundleIdentifier"] = "com.arcadematrix.dmdmediashowcase"
    plist_data["CFBundleShortVersionString"] = app_ver
    plist_data["CFBundleVersion"] = app_ver
    plist_data["LSMinimumSystemVersion"] = "12.0"
    plist_data["LSUIElement"] = True
    plist_data["NSHighResolutionCapable"] = True
    if built_icns:
        shutil.copy2(built_icns, final_app / "Contents" / "Resources" / "AppIcon.icns")
        plist_data["CFBundleIconFile"] = "AppIcon"
    with plist_path.open("wb") as fp:
        plistlib.dump(plist_data, fp)

    # Add standard `/Applications` drag-and-drop shortcut inside the .dmg
    os.symlink("/Applications", staging_dir / "Applications")

    dmg_path = DIST_DIR / f"DMD_Media_Showcase_{tag}.dmg"
    _sign_and_optionally_notarize_macos(final_app, dmg_path)

    shutil.rmtree(staging_dir, ignore_errors=True)
    print(f"[MACOS] Created native macOS installer: {dmg_path} ({dmg_path.stat().st_size / (1024 * 1024):.1f} MB)")
    return dmg_path


def _build_windows_zip(tag: str) -> Path:
    bundle_name = f"DMD_Media_Showcase_{tag}"
    pyi_cmd = _base_pyinstaller_args("dmd-media-showcase") + [str(ROOT / "app" / "main.py")]
    print(f"[WINDOWS] Running PyInstaller for {bundle_name} ...")
    subprocess.run(pyi_cmd, cwd=str(ROOT), check=True)

    raw_out = DIST_DIR / "dmd-media-showcase"
    target_dir = DIST_DIR / bundle_name
    if target_dir.exists():
        shutil.rmtree(target_dir)
    raw_out.rename(target_dir)

    internal_bin = target_dir / "_internal" / "bin"
    internal_bin.mkdir(parents=True, exist_ok=True)
    (target_dir / "data" / "stream_tmp").mkdir(parents=True, exist_ok=True)

    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        if found:
            shutil.copy2(found, internal_bin / f"{tool}.exe")

    # Windows-ONLY launcher (zero macOS/Linux files!)
    bat_path = target_dir / "Start_Showcase_Windows.bat"
    bat_path.write_text(
        "@echo off\r\n"
        "title DMD Media Showcase Server\r\n"
        "set DMD_PORTABLE_MODE=1\r\n"
        "echo Starting DMD Media Showcase Server on http://dmdmediashowcase.local:8890 (http://localhost:8890) and MQTT :1883 ...\r\n"
        'start "" http://localhost:8890/\r\n'
        '"%~dp0dmd-media-showcase.exe"\r\n',
        encoding="utf-8",
    )

    archive_base = DIST_DIR / bundle_name
    zip_path = Path(shutil.make_archive(str(archive_base), "zip", root_dir=DIST_DIR, base_dir=bundle_name))
    print(f"[WINDOWS] Created Windows portable archive: {zip_path} ({zip_path.stat().st_size / (1024 * 1024):.1f} MB)")
    return zip_path


def _build_linux_targz(tag: str) -> Path:
    bundle_name = f"DMD_Media_Showcase_{tag}"
    pyi_cmd = _base_pyinstaller_args("dmd-media-showcase") + [str(ROOT / "app" / "main.py")]
    print(f"[LINUX] Running PyInstaller for {bundle_name} ...")
    subprocess.run(pyi_cmd, cwd=str(ROOT), check=True)

    raw_out = DIST_DIR / "dmd-media-showcase"
    target_dir = DIST_DIR / bundle_name
    if target_dir.exists():
        shutil.rmtree(target_dir)
    raw_out.rename(target_dir)

    internal_bin = target_dir / "_internal" / "bin"
    internal_bin.mkdir(parents=True, exist_ok=True)
    (target_dir / "data" / "stream_tmp").mkdir(parents=True, exist_ok=True)

    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        if found:
            dst = internal_bin / tool
            shutil.copy2(found, dst)
            dst.chmod(dst.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # Linux-ONLY launcher (zero Windows .bat files!)
    sh_path = target_dir / "start_showcase.sh"
    sh_path.write_text(
        "#!/usr/bin/env bash\n"
        'set -e\n'
        'DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "export DMD_PORTABLE_MODE=1\n"
        'echo "Starting DMD Media Showcase Server on http://dmdmediashowcase.local:8890 (http://localhost:8890) and MQTT :1883 ..."\n'
        'exec "$DIR/dmd-media-showcase"\n',
        encoding="utf-8",
    )
    sh_path.chmod(sh_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    archive_base = DIST_DIR / bundle_name
    tar_path = Path(shutil.make_archive(str(archive_base), "gztar", root_dir=DIST_DIR, base_dir=bundle_name))
    print(f"[LINUX] Created Linux portable archive: {tar_path} ({tar_path.stat().st_size / (1024 * 1024):.1f} MB)")
    return tar_path


def build_portable_package() -> Path:
    tag = _detect_os_arch_tag()
    if sys.platform == "darwin":
        return _build_macos_dmg(tag)
    if sys.platform == "win32":
        return _build_windows_zip(tag)
    return _build_linux_targz(tag)


if __name__ == "__main__":
    build_portable_package()
