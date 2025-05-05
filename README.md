# SDUploader

**SDUploader** is a Python application for Windows that automatically detects connected SD cards and uploads supported media files (`.ARW`, `.JPEG`, `.MP4`) to a network SMB share. It ensures no duplicates are uploaded, maintains a log of uploaded files, and prevents data loss by cleaning up incomplete uploads.

## ✅ Features

- 🔄 **Automatic Upload** on SD card detection  
- 🗂️ Creates a dated folder (e.g., `2025-05-01`) on the SMB share  
- 🧠 Keeps track of uploaded files to prevent duplicates  
- 🚫 Cleans up partially uploaded files if the SD card is removed prematurely  
- 🔒 Prevents multiple instances from running simultaneously  
- ⚙️ Customizable file extensions and credentials

## ⚙️ Requirements

- Python 3.10+  
- Windows OS  
- SMB share available and accessible  
- Python modules:
  - `smbclient`
  - `hashlib`
  - `sqlite3`
  - `concurrent.futures`

Install required modules:
```bash
pip install smbprotocol
