# ==============================================================================
# Nova Files - Modern File Explorer & Local File Sharing
# ==============================================================================
# Yeh application ek modern Windows File Explorer hai jisme:
# 1. Sleek Dark Theme UI (PySide6 / Qt)
# 2. Windows Special Folders aur Registry Integration
# 3. Windows Junctions / Symlinks resolution (Win32 API ke zariye)
# 4. Standard File Operations (Copy, Cut, Paste, Rename, Delete, Search)
# 5. Local Wi-Fi par phone ke saath file download/upload karne ka feature (LAN Share)
# ==============================================================================

import ctypes
from ctypes import wintypes
import os
import shutil
import string
import sys
import winreg
from datetime import datetime
from pathlib import Path
import qrcode

# Special virtual path identifier jo "This PC" (saari drives dikhane) ke liye use hota hai
THIS_PC = "::this-pc"

# PySide6 (Qt) ke components jo GUI banane ke liye zaroori hain
from PySide6.QtCore import QFileInfo, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileIconProvider,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
    
)

# Wi-Fi sharing server jo phone se connect karne ke liye use hota hai
from lan_share import ShareServer


class FileManager(QMainWindow):
    """
    Main Application Window class jo poore File Explorer ko manage karti hai.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Nova Files")
        self.resize(1180, 720)

        # Current directory track karne ke liye (default: User ka Home folder)
        self.current_path = str(Path.home())

        # Back aur Forward navigation ke liye history stack
        self.history: list[str] = [self.current_path]
        self.history_index = 0

        # Clipboard: Copy ya Cut kiye gaye files/folders ke paths
        self.clipboard_paths: list[str] = []
        self.clipboard_cut = False  # True agar Cut kiya gaya ho, False agar Copy kiya ho

        # System file/folder ke default icons lene ke liye Qt provider
        self.icon_provider = QFileIconProvider()

        # Phone ke saath Wi-Fi share server ka instance (None jab share band ho)
        self.share_server: ShareServer | None = None

        # UI build karein, Dark theme styling apply karein aur files load karein
        self._build_ui()
        self._apply_style()
        self.refresh()

    # --------------------------------------------------------------------------
    # UI Building Methods (Layout & Widgets)
    # --------------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Poore window ka main layout aur structure taiyar karta hai."""
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 10)
        layout.setSpacing(12)

        # 1. Top Bar: Navigation buttons, Address bar, aur Search bar
        layout.addWidget(self._make_toolbar())

        # 2. Main Area: Left Sidebar (Quick Access) + Right Panel (File Table)
        # QSplitter se user dono panels ko resize kar sakta hai
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(8)
        splitter.addWidget(self._make_sidebar())
        splitter.addWidget(self._make_file_panel())
        splitter.setStretchFactor(0, 0)  # Sidebar fixed proportion
        splitter.setStretchFactor(1, 1)  # File list ko maximum space mile
        splitter.setSizes([230, 900])
        layout.addWidget(splitter, 1)

        # 3. Bottom Bar: Status bar (item count, path aur messages dikhane ke liye)
        self.status = QStatusBar()
        self.status.setSizeGripEnabled(False)
        self.setStatusBar(self.status)

    def _make_toolbar(self) -> QWidget:
        """Top navigation toolbar banata hai (Back, Forward, Up, Refresh, Path, Search)."""
        bar = QFrame()
        bar.setObjectName("toolbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)

        # Navigation Buttons
        self.back_btn = self._nav_button("‹", "Back", self.go_back)
        self.forward_btn = self._nav_button("›", "Forward", self.go_forward)
        self.up_btn = self._nav_button("↑", "Up", self.go_up)
        refresh_btn = self._nav_button("↻", "Refresh", self.refresh)

        # Address / Path Bar (Jisme current path dikhta hai aur user manually type kar sakta hai)
        self.path_input = QLineEdit()
        self.path_input.setObjectName("pathInput")
        self.path_input.setPlaceholderText("Enter a path and press Enter")
        self.path_input.returnPressed.connect(self.go_to_typed_path)

        # Real-time Search Box (Folder ke andar search filter karne ke liye)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("Search this folder")
        self.search_input.setFixedWidth(220)
        self.search_input.textChanged.connect(self.refresh)

        # Layout me widgets add karein
        row.addWidget(self.back_btn)
        row.addWidget(self.forward_btn)
        row.addWidget(self.up_btn)
        row.addWidget(refresh_btn)
        row.addWidget(self.path_input, 1)
        row.addWidget(self.search_input)
        return bar

    def _nav_button(self, text: str, tooltip: str, slot) -> QToolButton:
        """Helper method: Toolbar ke buttons ko standard style aur click event assign karta hai."""
        btn = QToolButton()
        btn.setText(text)
        btn.setToolTip(tooltip)
        btn.setObjectName("navButton")
        btn.setFixedSize(38, 36)
        btn.clicked.connect(slot)
        return btn

    def _make_sidebar(self) -> QWidget:
        """Left Sidebar banata hai jisme Quick Access shortcuts aur action buttons hote hain."""
        panel = QFrame()
        panel.setObjectName("sidebar")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(6)

        # App Title & Section Header
        title = QLabel("Nova Files")
        title.setObjectName("appTitle")
        subtitle = QLabel("Quick access")
        subtitle.setObjectName("sectionLabel")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        # Quick Access list widget
        self.places = QListWidget()
        self.places.setObjectName("places")
        self.places.setFocusPolicy(Qt.NoFocus)
        home = Path.home()

        # Windows ke special folders (Registry se verify kiye hue paths)
        shortcuts = [
            ("Home", str(home)),
            ("Desktop", self._known_folder("Desktop", home / "Desktop")),
            ("Documents", self._known_folder("Personal", home / "Documents")),
            ("Downloads", self._known_folder("{374DE290-123F-4565-9164-39C4925E467B}", home / "Downloads")),
            ("Pictures", self._known_folder("My Pictures", home / "Pictures")),
            ("Music", self._known_folder("My Music", home / "Music")),
            ("Videos", self._known_folder("My Video", home / "Videos")),
            ("This PC", THIS_PC),
        ]

        # Sirf exist karne wale folders ko hi list me add karein
        for name, path in shortcuts:
            if path != THIS_PC and not os.path.exists(path):
                continue
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, path)
            item.setSizeHint(QSize(0, 38))
            self.places.addItem(item)
        self.places.itemClicked.connect(self._open_place)

        layout.addWidget(self.places, 1)

        # Action Buttons: New Folder & Phone Share
        new_folder = QPushButton("+  New folder")
        new_folder.setObjectName("accentButton")
        new_folder.clicked.connect(self.create_folder)

        self.share_btn = QPushButton("Share to phone")
        self.share_btn.setObjectName("ghostButton")
        self.share_btn.clicked.connect(self.toggle_share)

        layout.addWidget(new_folder)
        layout.addWidget(self.share_btn)
        return panel

    def _make_file_panel(self) -> QWidget:
        """Right Side ka main file table widget banata hai."""
        panel = QFrame()
        panel.setObjectName("filePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        # 4 Columns: Name, Type, Size, Modified Date
        self.table = QTableWidget(0, 4)
        self.table.setObjectName("fileTable")
        self.table.setHorizontalHeaderLabels(["Name", "Type", "Size", "Modified"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setIconSize(QSize(22, 22))
        self.table.verticalHeader().setDefaultSectionSize(36)

        # Table Header column resizing rules
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.Stretch)           # Name column baaki bachi saari jagah lega
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Type
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Size
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # Modified Date

        # Double click se open karein aur Right-click par context menu dikhayein
        self.table.cellDoubleClicked.connect(self._open_row)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.table)
        return panel

    # --------------------------------------------------------------------------
    # Windows Registry & Special Folder Detection
    # --------------------------------------------------------------------------

    def _known_folder(self, registry_name: str, fallback: Path) -> str:
        """
        Windows Registry se user folders (Documents, Downloads, Desktop etc.) ka actual path nikaalta hai.
        Yeh OneDrive redirection ke case me bhi sahi path detect kar leta hai.
        """
        for key_path in (
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        ):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    value, _ = winreg.QueryValueEx(key, registry_name)
                path = os.path.expandvars(str(value))
                if os.path.isdir(path):
                    return path
            except OSError:
                continue

        # Fallback agar registry me na mile to OneDrive ya standard folder check karein
        for candidate in (
            fallback,
            Path.home() / "OneDrive" / fallback.name,
            Path.home() / "OneDrive" / "Desktop" if fallback.name == "Desktop" else fallback,
        ):
            if candidate.is_dir():
                return str(candidate)
        return str(fallback)

    def _open_place(self, item: QListWidgetItem) -> None:
        """Sidebar item click hone par us folder me navigate karta hai."""
        self.navigate(item.data(Qt.UserRole))

    # --------------------------------------------------------------------------
    # Wi-Fi / LAN Phone Sharing Feature
    # --------------------------------------------------------------------------

    def toggle_share(self) -> None:
        """
        Phone Share server ko ON ya OFF karta hai.
        ON hone par local IP, Port aur 4-digit PIN ka dialog show karta hai.
        """
        # Agar share pehle se chal raha hai to stop karein
        if self.share_server:
            self.share_server.stop()
            self.share_server = None
            self.share_btn.setText("Share to phone")
            self.status.showMessage("Phone share band ho gaya")
            return

        # Current open folder ko share karein (agar This PC par ho to Home folder share karein)
        folder = str(Path.home()) if self.current_path == THIS_PC else self.current_path
        try:
            server = ShareServer(folder)
            server.start()
        except OSError as exc:
            QMessageBox.warning(self, "Share", f"Share start nahi hua:\n{exc}")
            return

        self.share_server = server
        self.share_btn.setText("Stop phone share")

        # Phone connection information popup dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("Phone se connect karo")
        dialog.setMinimumWidth(420)
        box = QVBoxLayout(dialog)

        title = QLabel("Same Wi-Fi par phone browser kholo")
        title.setObjectName("appTitle")

        url = QLabel(server.url)
        url.setTextInteractionFlags(Qt.TextSelectableByMouse)
        url.setObjectName("shareUrl")
        img = qrcode.make(server.url)
        img.save("qrcode.png")
        qrimage = QLabel(dialog)
        qrimage.setObjectName("qrimage")
        qr = QPixmap("qrcode.png")
        # qr.setObjectName("qrimage")
        qrimage.setPixmap(qr)
        box.addWidget(qrimage, alignment=Qt.AlignCenter)
        # os.remove("qrcode.png")

        pin = QLabel(f"PIN: {server.pin}")
        pin.setObjectName("sharePin")

        hint = QLabel(
            f"Jo folder share ho raha hai:\n{folder}\n\n"
            "1. Phone aur PC same Wi-Fi pe hon\n"
            "2. Phone Chrome/Safari mein ye URL kholo\n"
            "3. PIN daalo\n"
            "4. File download/upload karo\n\n"
            "Windows Firewall poochhe to Allow karo."
        )
        hint.setWordWrap(True)

        copy_btn = QPushButton("Copy link")
        copy_btn.setObjectName("accentButton")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(server.url))

        close_btn = QPushButton("OK")
        close_btn.setObjectName("ghostButton")
        close_btn.clicked.connect(dialog.accept)

        box.addWidget(title)
        box.addWidget(url)
        box.addWidget(pin)
        box.addWidget(hint)
        box.addWidget(copy_btn)
        box.addWidget(close_btn)
        dialog.exec()

        self.status.showMessage(f"Sharing {folder}  ·  {server.url}  ·  PIN {server.pin}")

    def closeEvent(self, event) -> None:
        """Window close hone par background server ko gracefully band karta hai."""
        if self.share_server:
            self.share_server.stop()
            self.share_server = None
        super().closeEvent(event)

    # --------------------------------------------------------------------------
    # Navigation & Path Handling
    # --------------------------------------------------------------------------

    def navigate(self, path: str, add_history: bool = True) -> None:
        """
        Naye path par navigate karta hai, permissions check karta hai aur history update karta hai.
        """
        if path == THIS_PC:
            target = THIS_PC
        else:
            # Junctions / Symbolic links ko resolve karein
            target = self._resolve_folder(os.path.abspath(os.path.expanduser(path)))
            if not os.path.isdir(target):
                QMessageBox.warning(self, "Nova Files", f"Folder nahi mila:\n{target}")
                return
            if not self._can_list(target):
                self._show_access_denied(target)
                return

        self.current_path = target

        # Navigation History update karein (Back/Forward ke liye)
        if add_history:
            self.history = self.history[: self.history_index + 1]
            if not self.history or self.history[-1] != target:
                self.history.append(target)
                self.history_index = len(self.history) - 1

        self.refresh()

    def _resolve_folder(self, path: str) -> str:
        """
        Windows junctions (jaise Documents\\My Videos -> Videos) ko unke asli target path par resolve karta hai.
        """
        resolved = self._final_path(path) or self._read_reparse_target(path) or self._known_folder_alias(path)
        if resolved and os.path.isdir(resolved):
            return os.path.normpath(resolved)
        return path

    def _final_path(self, path: str) -> str | None:
        """
        Windows kernel32 API (GetFinalPathNameByHandleW) use karke kisi file/folder ka canonical real path nikaalta hai.
        """
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.GetFinalPathNameByHandleW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        GENERIC_READ = 0x80000000
        FILE_SHARE = 0x1 | 0x2 | 0x4
        OPEN_EXISTING = 3
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        invalid = ctypes.c_void_p(-1).value

        # File handle open karein
        handle = kernel32.CreateFileW(
            path, 0, FILE_SHARE, None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None
        )
        if handle in (None, 0, invalid):
            handle = kernel32.CreateFileW(
                path,
                GENERIC_READ,
                FILE_SHARE,
                None,
                OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS,
                None,
            )
        if handle in (None, 0, invalid):
            return None
        try:
            buf = ctypes.create_unicode_buffer(512)
            length = kernel32.GetFinalPathNameByHandleW(handle, buf, 512, 0)
            if not length:
                return None
            return self._normalize_nt_path(buf.value)
        finally:
            kernel32.CloseHandle(handle)

    def _read_reparse_target(self, path: str) -> str | None:
        """Agar path symlink ya junction hai to target path read karta hai."""
        try:
            if not (os.path.islink(path) or os.path.isjunction(path)):
                return None
            return self._normalize_nt_path(os.readlink(path))
        except OSError:
            return None

    def _known_folder_alias(self, path: str) -> str | None:
        """Windows ke legacy folder aliases ko map karta hai."""
        aliases = {
            "my videos": Path.home() / "Videos",
            "my music": Path.home() / "Music",
            "my pictures": Path.home() / "Pictures",
        }
        target = aliases.get(Path(path).name.lower())
        if target and target.is_dir():
            return str(target)
        return None

    def _normalize_nt_path(self, path: str) -> str:
        """Windows NT kernel ke device prefixes (\\\\?\\ etc.) ko clean karke standard path banata hai."""
        cleaned = path
        for prefix in ("\\\\?\\UNC\\", "\\??\\UNC\\", "\\\\?\\", "\\??\\"):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
                if prefix.endswith("UNC\\"):
                    cleaned = "\\\\" + cleaned
                break
        return os.path.abspath(cleaned)

    def _can_list(self, path: str) -> bool:
        """Check karta hai ki user ke paas folder read karne ki permission hai ya nahi."""
        try:
            with os.scandir(path) as scan:
                next(scan, None)
            return True
        except (PermissionError, OSError):
            return False

    def _show_access_denied(self, path: str) -> None:
        """Permission na hone par user ko friendly warning dialog dikhata hai."""
        QMessageBox.warning(
            self,
            "Access nahi mila",
            "Is folder ka access nahi hai.\n"
            "Windows ne is folder ko lock kiya hai.\n\n"
            f"{path}",
        )

    def _list_drives(self) -> list[str]:
        """Windows API bitmask use karke system ke sabhi active drives (C:\\, D:\\, etc.) list karta hai."""
        drives = []
        try:
            import ctypes

            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for index, letter in enumerate(string.ascii_uppercase):
                if bitmask & (1 << index):
                    drives.append(f"{letter}:\\")
        except Exception:
            # Fallback agar API call fail ho jaye
            drives = [f"{letter}:\\" for letter in string.ascii_uppercase if os.path.exists(f"{letter}:\\")]
        return drives

    # --------------------------------------------------------------------------
    # Navigation Actions (Back, Forward, Up, Manual Path)
    # --------------------------------------------------------------------------

    def go_back(self) -> None:
        """Pichhle folder me wapas jata hai."""
        if self.history_index > 0:
            self.history_index -= 1
            self.current_path = self.history[self.history_index]
            self.refresh()

    def go_forward(self) -> None:
        """Agle folder par aage badhta hai."""
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self.current_path = self.history[self.history_index]
            self.refresh()

    def go_up(self) -> None:
        """Parent folder me jata hai (Ek level upar)."""
        if self.current_path == THIS_PC:
            return
        parent = str(Path(self.current_path).parent)
        if parent == self.current_path:
            # Root drive par hone par 'This PC' view par le jayein
            self.navigate(THIS_PC)
            return
        self.navigate(parent)

    def go_to_typed_path(self) -> None:
        """Address bar me user dwara type kiye gaye path par jata hai."""
        self.navigate(self.path_input.text().strip())

    # --------------------------------------------------------------------------
    # Table Population & File Listing
    # --------------------------------------------------------------------------

    def refresh(self) -> None:
        """
        Current directory ke sabhi files/folders ko read karke table me display karta hai.
        Search filter aur sorting bhi handle karta hai.
        """
        display_path = "This PC" if self.current_path == THIS_PC else self.current_path
        self.path_input.setText(display_path)
        self.back_btn.setEnabled(self.history_index > 0)
        self.forward_btn.setEnabled(self.history_index < len(self.history) - 1)
        query = self.search_input.text().strip().lower()

        # Temporary sorting disable karein taaki rows fast insert ho sakein
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        visible = 0
        skipped = 0

        # Agar "This PC" view par hain to drives dikhayein
        if self.current_path == THIS_PC:
            for drive in self._list_drives():
                if query and query not in drive.lower():
                    continue
                self._add_path_row(drive, drive.rstrip("\\"), True)
                visible += 1
        else:
            # Normal folder view: os.scandir se entries read karein
            try:
                raw_entries = list(os.scandir(self.current_path))
            except PermissionError:
                self._show_access_denied(self.current_path)
                return
            except OSError as exc:
                QMessageBox.warning(self, "Nova Files", str(exc))
                return

            # Folders aur Files ko alag karein taaki folders pehle dikhein
            folders = []
            files = []
            for entry in raw_entries:
                is_dir = self._entry_is_dir(entry)
                if is_dir is None:
                    skipped += 1
                    continue
                if is_dir:
                    folders.append(entry)
                else:
                    files.append(entry)

            # Name ke according alphabetically sort karein
            folders.sort(key=lambda e: e.name.lower())
            files.sort(key=lambda e: e.name.lower())

            # Table me rows add karein (Folders pehle, fir Files)
            for entry in folders + files:
                if query and query not in entry.name.lower():
                    continue
                try:
                    self._add_row(entry)
                    visible += 1
                except OSError:
                    skipped += 1

        self.table.setSortingEnabled(True)
        extra = f"  ·  {skipped} locked" if skipped else ""
        self.status.showMessage(f"{visible} items{extra}  ·  {display_path}")

    def _entry_is_dir(self, entry: os.DirEntry) -> bool | None:
        """Check karta hai ki di gayi entry directory hai ya file (permission errors ko gracefully handle karta hai)."""
        try:
            return entry.is_dir(follow_symlinks=False)
        except (PermissionError, OSError):
            try:
                return os.path.isdir(entry.path)
            except OSError:
                return None

    def _add_row(self, entry: os.DirEntry) -> None:
        """Entry ko resolve karke table me add karne ke liye pass karta hai."""
        is_dir = bool(self._entry_is_dir(entry))
        path = self._resolve_folder(entry.path) if is_dir else entry.path
        self._add_path_row(path, entry.name, is_dir, entry)

    def _add_path_row(
        self,
        path: str,
        name: str,
        is_dir: bool,
        entry: os.DirEntry | None = None,
    ) -> None:
        """Table me 1 row insert karta hai: Icon+Name, Type, Size, aur Modified Date."""
        row = self.table.rowCount()
        self.table.insertRow(row)

        # Column 0: File / Folder Icon & Name
        icon = self.icon_provider.icon(QFileInfo(path))
        name_item = QTableWidgetItem(icon, name)
        name_item.setData(Qt.UserRole, path)
        name_item.setData(Qt.UserRole + 1, is_dir)

        # Column 1: Type (Drive / Folder / File extension)
        if is_dir:
            kind = "Drive" if len(path) <= 3 else "Folder"
        else:
            kind = Path(name).suffix[1:].upper() or "File"
        type_item = QTableWidgetItem(kind)

        # Column 2: Size (Files ke liye formatted size, folders ke liye '—')
        size_item = QTableWidgetItem(self._format_size(entry) if entry and not is_dir else "—")
        size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # Column 3: Last Modified Date & Time
        modified = "—"
        try:
            stamp = entry.stat().st_mtime if entry else os.path.getmtime(path)
            modified = datetime.fromtimestamp(stamp).strftime("%Y-%m-%d  %H:%M")
        except OSError:
            pass
        date_item = QTableWidgetItem(modified)

        # Items ko read-only banayein (Direct cell editing disable rahe)
        for item in (name_item, type_item, size_item, date_item):
            item.setFlags(item.flags() ^ Qt.ItemIsEditable)

        self.table.setItem(row, 0, name_item)
        self.table.setItem(row, 1, type_item)
        self.table.setItem(row, 2, size_item)
        self.table.setItem(row, 3, date_item)

    def _format_size(self, entry: os.DirEntry) -> str:
        """Bytes ko human-readable format (B, KB, MB, GB, TB) me convert karta hai."""
        try:
            size = entry.stat().st_size
        except OSError:
            return "—"
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"

    # --------------------------------------------------------------------------
    # File Operations (Open, Context Menu, Copy, Cut, Paste, Delete, Rename)
    # --------------------------------------------------------------------------

    def _selected_paths(self) -> list[str]:
        """Table me currently selected sabhi items ke full paths return karta hai."""
        rows = {index.row() for index in self.table.selectedIndexes()}
        paths = []
        for row in sorted(rows):
            item = self.table.item(row, 0)
            if item:
                paths.append(item.data(Qt.UserRole))
        return paths

    def _open_row(self, row: int, _column: int) -> None:
        """Row par double click ya Enter dabane par folder open karta hai ya default app me file kholta hai."""
        item = self.table.item(row, 0)
        if not item:
            return
        path = item.data(Qt.UserRole)
        # Agar folder ya drive hai to navigate karein
        if path == THIS_PC or os.path.isdir(path):
            self.navigate(path)
            return
        # Agar file hai to Windows ke default app me open karein
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except OSError as exc:
            QMessageBox.warning(self, "Nova Files", str(exc))

    def _show_context_menu(self, pos) -> None:
        """Right-click karne par options menu (Open, Copy, Cut, Paste, Delete, Rename etc.) dikhata hai."""
        menu = QMenu(self)
        paths = self._selected_paths()

        open_action = menu.addAction("Open")
        menu.addSeparator()
        new_folder = menu.addAction("New folder")
        menu.addSeparator()
        copy_action = menu.addAction("Copy")
        cut_action = menu.addAction("Cut")
        paste_action = menu.addAction("Paste")
        menu.addSeparator()
        rename_action = menu.addAction("Rename")
        delete_action = menu.addAction("Delete")
        menu.addSeparator()
        copy_path = menu.addAction("Copy path")

        # Conditions ke hisaab se actions enable/disable karein
        paste_action.setEnabled(bool(self.clipboard_paths))
        has_selection = bool(paths)
        for action in (open_action, copy_action, cut_action, rename_action, delete_action, copy_path):
            action.setEnabled(has_selection)
        rename_action.setEnabled(len(paths) == 1)

        # User ka chosen action execute karein
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == open_action and paths:
            self._open_row(self.table.currentRow(), 0)
        elif chosen == new_folder:
            self.create_folder()
        elif chosen == copy_action:
            self.clipboard_paths = paths
            self.clipboard_cut = False
        elif chosen == cut_action:
            self.clipboard_paths = paths
            self.clipboard_cut = True
        elif chosen == paste_action:
            self.paste_items()
        elif chosen == rename_action:
            self.rename_item(paths[0])
        elif chosen == delete_action:
            self.delete_items(paths)
        elif chosen == copy_path and paths:
            QApplication.clipboard().setText("\n".join(paths))

    def create_folder(self) -> None:
        """Naya folder create karne ke liye prompt popup karta hai."""
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if not ok or not name.strip():
            return
        target = Path(self.current_path) / name.strip()
        try:
            target.mkdir(exist_ok=False)
        except OSError as exc:
            QMessageBox.warning(self, "Nova Files", str(exc))
            return
        self.refresh()

    def rename_item(self, path: str) -> None:
        """File ya folder ka naam badalne (Rename) ke liye prompt karta hai."""
        old = Path(path)
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=old.name)
        if not ok or not name.strip() or name.strip() == old.name:
            return
        try:
            old.rename(old.with_name(name.strip()))
        except OSError as exc:
            QMessageBox.warning(self, "Nova Files", str(exc))
            return
        self.refresh()

    def delete_items(self, paths: list[str]) -> None:
        """Selected files/folders ko confirmation ke baad permanently delete karta hai."""
        if not paths:
            return
        confirm = QMessageBox.question(
            self,
            "Delete",
            f"Delete {len(paths)} item(s)? This cannot be undone.",
        )
        if confirm != QMessageBox.Yes:
            return
        for path in paths:
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except OSError as exc:
                QMessageBox.warning(self, "Nova Files", str(exc))
        self.refresh()

    def paste_items(self) -> None:
        """Clipboard se copied/cut files ko current folder me paste karta hai."""
        for source in self.clipboard_paths:
            dest = Path(self.current_path) / Path(source).name
            # Duplicate name handle karne ke liye 'copy_' prefix lagayein
            if dest.exists():
                dest = Path(self.current_path) / f"copy_{Path(source).name}"
            try:
                if self.clipboard_cut:
                    shutil.move(source, dest)
                elif os.path.isdir(source):
                    shutil.copytree(source, dest)
                else:
                    shutil.copy2(source, dest)
            except OSError as exc:
                QMessageBox.warning(self, "Nova Files", str(exc))

        # Cut operation complete hone par clipboard reset karein
        if self.clipboard_cut:
            self.clipboard_paths = []
            self.clipboard_cut = False
        self.refresh()

    # --------------------------------------------------------------------------
    # Keyboard Shortcuts Handler
    # --------------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        """Keyboard keys (Ctrl+C, Ctrl+V, Delete, F2, F5 etc.) ko handle karta hai."""
        if event.matches(QKeySequence.Copy):
            self.clipboard_paths = self._selected_paths()
            self.clipboard_cut = False
        elif event.matches(QKeySequence.Cut):
            self.clipboard_paths = self._selected_paths()
            self.clipboard_cut = True
        elif event.matches(QKeySequence.Paste):
            self.paste_items()
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and event.modifiers() == Qt.NoModifier:
            if event.key() == Qt.Key_Delete:
                self.delete_items(self._selected_paths())
            else:
                self.go_up()  # Backspace dabane par parent directory me jayein
        elif event.key() == Qt.Key_Return:
            self._open_row(self.table.currentRow(), 0)
        elif event.key() == Qt.Key_F5:
            self.refresh()
        elif event.key() == Qt.Key_F2:
            paths = self._selected_paths()
            if len(paths) == 1:
                self.rename_item(paths[0])
        else:
            super().keyPressEvent(event)

    # --------------------------------------------------------------------------
    # UI Theme & Styling (QSS / CSS)
    # --------------------------------------------------------------------------

    def _apply_style(self) -> None:
        """Application ka sleek Dark Theme CSS set karta hai."""
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #0f1117;
                color: #e8eaed;
                font-family: "Segoe UI", "Inter", sans-serif;
                font-size: 13px;
            }
            #toolbar, #sidebar, #filePanel {
                background: #161922;
                border: 1px solid #262b38;
                border-radius: 16px;
            }
            #appTitle {
                font-size: 20px;
                font-weight: 700;
                color: #f4f6fb;
                padding: 2px 6px 8px 6px;
            }
            #sectionLabel {
                color: #8b93a7;
                font-size: 11px;
                letter-spacing: 1px;
                text-transform: uppercase;
                padding: 0 6px 4px 6px;
            }
            #navButton {
                background: #222737;
                color: #dce2f0;
                border: 1px solid #2f3546;
                border-radius: 10px;
                font-size: 16px;
            }
            #navButton:hover { background: #2b3144; }
            #navButton:disabled { color: #5d6478; }
            #pathInput, #searchInput {
                background: #10131b;
                color: #e8eaed;
                border: 1px solid #2a3142;
                border-radius: 10px;
                padding: 8px 12px;
                selection-background-color: #4f7cff;
            }
            #pathInput:focus, #searchInput:focus {
                border: 1px solid #4f7cff;
            }
            #places {
                background: transparent;
                border: none;
                outline: none;
            }
            #places::item {
                padding: 8px 12px;
                margin: 2px 0;
                border-radius: 10px;
                color: #cdd3e1;
            }
            #places::item:hover { background: #222737; }
            #places::item:selected {
                background: #2a3a68;
                color: #ffffff;
            }
            #accentButton {
                background: #4f7cff;
                color: white;
                border: none;
                border-radius: 12px;
                padding: 10px 14px;
                font-weight: 600;
            }
            #accentButton:hover { background: #6b91ff; }
            #ghostButton {
                background: #222737;
                color: #dce2f0;
                border: 1px solid #2f3546;
                border-radius: 12px;
                padding: 10px 14px;
                font-weight: 600;
            }
            #ghostButton:hover { background: #2b3144; }
            #shareUrl {
                font-size: 18px;
                font-weight: 700;
                color: #6b91ff;
                padding: 8px 0;
            }
            #sharePin {
                font-size: 28px;
                font-weight: 700;
                color: #f4f6fb;
                padding-bottom: 8px;
            }
            QDialog { background: #161922; }
            #fileTable {
                background: transparent;
                border: none;
                gridline-color: transparent;
                selection-background-color: #2a3a68;
                selection-color: #ffffff;
                alternate-background-color: #12151d;
            }
            #fileTable::item { padding: 4px 8px; }
            QHeaderView::section {
                background: #161922;
                color: #9aa3b8;
                border: none;
                border-bottom: 1px solid #262b38;
                padding: 10px 8px;
                font-weight: 600;
            }
            QStatusBar {
                background: transparent;
                color: #8b93a7;
                border: none;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 8px 2px;
            }
            QScrollBar::handle:vertical {
                background: #32384a;
                border-radius: 5px;
                min-height: 30px;
            }
            QMenu {
                background: #1b2030;
                color: #e8eaed;
                border: 1px solid #2f3546;
                border-radius: 10px;
                padding: 6px;
            }
            QMenu::item {
                padding: 8px 18px;
                border-radius: 8px;
            }
            QMenu::item:selected { background: #2a3a68; }
            QMessageBox, QInputDialog {
                background: #161922;
            }
            QSplitter::handle { background: transparent; }

            #qrimage {
                padding: 8px;
                border-radius: 12px;
                border: 1px solid #2a3a68;

                
            }
            """
        )


# ==============================================================================
# Application Entry Point
# ==============================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # Modern cross-platform Qt base style
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    window = FileManager()
    window.show()
    sys.exit(app.exec())
