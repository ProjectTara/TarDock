# TaraDock

Dock ala macOS untuk Windows. App yang di-pin di taskbar tampil di bawah tengah layar.

## Fitur
- Sinkron otomatis dengan pin taskbar Windows (pin / unpin langsung terlihat).
- Magnifikasi saat hover (mati, 1.3x, 1.6x, 2.0x) dan label nama app.
- Titik di bawah ikon untuk app yang sedang berjalan.
- Klik: buka app; jika sudah terbuka, pindah ke jendelanya; klik lagi untuk minimize. Banyak jendela: klik berulang untuk berganti.
- Shift+klik atau klik tengah: jendela baru.
- Klik kanan ikon: tutup, geser kiri/kanan, sembunyikan, buka lokasi file.
- App berjalan yang tidak di-pin tampil setelah garis pemisah (bisa dimatikan).
- Dock otomatis sembunyi saat game / video fullscreen.
- Opsi: auto-hide dock, sisakan ruang (jendela maximize tidak tertutup dock), auto-hide taskbar Windows, autostart.

Semua pengaturan ada di ikon tray (klik kanan) atau klik kanan area kosong dock.

## Unduh
Ambil `TaraDock.exe` dari halaman [Releases](../../releases). Tidak perlu install.

## Batasan v1
- Hanya di monitor utama.
- App Store (UWP) yang di-pin tidak menampilkan titik "berjalan"; jendelanya muncul di bagian app berjalan.
- PWA browser yang di-pin ikut tertandai berjalan saat browsernya terbuka.

## Rilis versi baru
1. Ubah `__version__` di `taradock.py`.
2. Commit, lalu tab **Actions** > **Build & Release** > **Run workflow** > isi versi yang sama.
3. Actions menguji, membangun exe, dan menerbitkan rilis.

## Jalankan dari source
`run.bat` (memakai `%USERPROFILE%\python312` jika ada).
