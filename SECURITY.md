# Keamanan TaraDock

## Yang dilakukan app
- Membaca shortcut di `%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar` (pin taskbar milik user).
- Membaca daftar jendela terbuka (judul + path exe) untuk titik "sedang berjalan". Data ini tidak disimpan dan tidak dikirim.
- Menjalankan shortcut / exe yang diklik lewat `os.startfile`, tanpa shell dan tanpa argumen tambahan.
- Menyimpan pengaturan di `%APPDATA%\TaraDock\config.json`. Isi divalidasi; file rusak diganti default dan disimpan sebagai `config.json.bad`.

## Yang tidak dilakukan
- Tidak ada koneksi jaringan. Menu "Cek update" hanya membuka halaman Releases di browser.
- Tidak butuh hak admin. Tidak memasang service, driver, atau hook keyboard.
- Tidak ada auto-update yang mengunduh kode.

## Perubahan sistem (hanya jika diaktifkan user)
| Opsi | Perubahan | Dipulihkan |
|---|---|---|
| Sembunyikan taskbar Windows | Auto-hide taskbar (`ABM_SETSTATE`) | Saat opsi dimatikan atau app keluar. Jika app crash, dipulihkan saat start berikutnya. |
| Sisakan ruang untuk dock | Mendaftarkan AppBar di tepi bawah | Dihapus saat keluar. Jika crash, hilang setelah Explorer restart / logout. |
| Jalankan saat Windows mulai | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\TaraDock` | Dihapus saat opsi dimatikan. |

## Rantai build
- Exe dibangun GitHub Actions dari commit yang ditandai, bukan dari PC pribadi.
- Semua action dipin ke commit SHA; library Python dipin versinya.
- Job build hanya punya izin baca. Izin tulis hanya di job rilis.
- Selftest dijalankan pada source dan pada exe hasil build sebelum rilis.
- Setiap rilis menyertakan `SHA256SUMS.txt` dan attestation asal-usul build.

Verifikasi exe yang diunduh:
```
certutil -hashfile TaraDock.exe SHA256
gh attestation verify TaraDock.exe --repo ProjectTara/TaraDock
```

Exe belum ditandatangani (unsigned), jadi SmartScreen bisa menampilkan peringatan.
