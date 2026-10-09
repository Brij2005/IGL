import sys
import os
import requests
import base64
import json
import cv2
import numpy as np

def run_test():
    img_b64 = None
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        with open(sys.argv[1], "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
        print(f"Using image from file: {sys.argv[1]}")
    else:
        # Generate synthetic test frame (320x320 color canvas)
        test_img = np.zeros((320, 320, 3), dtype=np.uint8)
        cv2.putText(test_img, "IGL Test Frame", (20, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        _, buf = cv2.imencode(".jpg", test_img)
        img_b64 = base64.b64encode(buf).decode("utf-8")
        print("Generated synthetic test frame for validation.")

    print("Sending POST request to http://127.0.0.1:8000/api/yolo/detect (conf=0.10, imgsz=640)...")
    res = requests.post("http://127.0.0.1:8000/api/yolo/detect", json={
        "image_base64": f"data:image/jpeg;base64,{img_b64}",
        "conf": 0.10,
        "imgsz": 640
    })

    print("HTTP Response Code:", res.status_code)
    try:
        data = res.json()
        print("API Response JSON:")
        print(json.dumps(data, indent=2))
        return res.status_code == 200
    except Exception as e:
        print("Failed to parse response JSON:", e)
        print("Raw response:", res.text)
        return False

if __name__ == "__main__":
    success = run_test()
    sys.exit(0 if success else 1)
