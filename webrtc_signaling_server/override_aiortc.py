import os
import shutil
import site
import sys

# File chỉnh sửa nằm cùng thư mục với script
current_dir = os.path.dirname(os.path.abspath(__file__))
source_file = os.path.join(current_dir, "rtcrtpreceiver.py")

if not os.path.exists(source_file):
    print(f"Not found {source_file}")
    sys.exit(1)

# Tìm site-packages đang dùng
site_packages_dirs = site.getsitepackages()

target_file = None
for d in site_packages_dirs:
    candidate = os.path.join(d, "aiortc", "rtcrtpreceiver.py")
    if os.path.exists(candidate):
        target_file = candidate
        break

if not target_file:
    print("Not found rtcrtpreceiver.py site-packages")
    sys.exit(1)

print(f"File: {target_file}")
print("Overriding ...")

# Copy file mới
shutil.copy(source_file, target_file)

print("Complete")
