import sys
import os

print("Current working directory:", os.getcwd())
print("Python path (sys.path):")
for p in sys.path:
    print(f"  - {p}")

try:
    print("\nAttempting to import backend.shared.database...")
    from backend.shared.database import get_db
    print("Successfully imported backend.shared.database")
except ImportError as e:
    print(f"Failed to import backend.shared.database: {e}")

try:
    print("\nAttempting to import backend.services.vendor_selection...")
    from backend.services.vendor_selection import assign_vendor_for_job
    print("Successfully imported backend.services.vendor_selection")
except ImportError as e:
    print(f"Failed to import backend.services.vendor_selection: {e}")