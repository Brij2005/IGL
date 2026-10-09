# 🛡️ IGL VisionGuard AI — Industrial AI Safety Intelligence System

[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/Frontend-React%2018%20+%20Vite%205-61DAFB.svg?logo=react&logoColor=black)](https://react.dev/)
[![YOLOv8](https://img.shields.io/badge/Vision-Ultralytics%20YOLOv8-00FFFF.svg)](https://ultralytics.com)
[![TensorFlow.js](https://img.shields.io/badge/CV-TensorFlow.js%20%2B%20Face--API-FF6F00.svg?logo=tensorflow&logoColor=white)](https://www.tensorflow.org/js)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**IGL VisionGuard AI** is an industrial-grade computer vision and safety intelligence platform designed for manufacturing plants, refineries, conveyor packaging lines, and hazardous work zones.

---

## 🌟 Key Capabilities

1. **PPE Compliance & Helmet Monitoring:**
   - Real-time worker upper-body and head crop analysis to detect safety helmets with temporal persistence filters to eliminate false flickers.

2. **Two-Pass Mobile Phone Misuse Detection:**
   - Advanced two-pass ROI scanning architecture for reliable phone detection even when held close to the face or ears.
   - 3-tier warning escalation state machine with audible Web Audio siren alarms.

3. **Conveyor Packaging Quality Inspection:**
   - High-speed bottle inspection engine analyzing Liquid Fill Levels (Pass / Low / High), Cap Integrity (Missing / Misaligned / Damaged), and Surface/Glass Body Defects with automated air-jet reject simulation.

4. **Dangerous Zone & Near-Miss Proximity Radar:**
   - Live interactive 2D spatial radar tracking worker-vehicle (forklift/machinery) distances against dynamic 2.0m hazardous perimeter boundaries.

5. **Personnel Identity & Schedule Verification:**
   - Face template verification against registered rosters (`@vladmandic/face-api`).
   - Time-window rule verification with automated WhatsApp Cloud API safety notifications (Mock & Live mode).

6. **Dual Mode AI Image & Camera Scanner:**
   - Live camera stream inspection and drag-and-drop static image inspection with class breakdowns and confidence scores.

---

## 🏗️ Architecture Overview

```
┌────────────────────────────────────────────────────────┐
│                    Frontend Client                     │
│               (React 18 + Vite 5 + TF.js)              │
│  - Live Video Viewport & 60 FPS Canvas Rendering       │
│  - Face Landmark & Identity Recognition                │
│  - Real-Time Warning Escalation State Machine          │
└──────────────────────────┬─────────────────────────────┘
                           │ HTTP REST / JSON
                           ▼
┌────────────────────────────────────────────────────────┐
│                   Backend API Server                   │
│                   (FastAPI + Uvicorn)                  │
│  - PyTorch YOLOv8 Detection Engine (/api/yolo/detect)  │
│  - Camera Stream & Zone Configuration Management       │
│  - WhatsApp Cloud API Notification Dispatcher          │
└──────────────────────────┬─────────────────────────────┘
                           │ SQLite3 (WAL Mode)
                           ▼
┌────────────────────────────────────────────────────────┐
│                     Local Storage                      │
│                  (backend/igl_safety.db)               │
│  - Cameras, Alerts, Observations, Rules, Health Logs   │
└────────────────────────────────────────────────────────┘
```

---

## 📁 Repository Structure

```
.
├── backend/
│   ├── .env.example             # Backend environment template
│   ├── ai_engine.py             # Geometric analysis & frame quality checks
│   ├── database.py              # SQLite schema & table initialization
│   ├── debug_yolo_phone.py      # Diagnostic CLI utility for YOLO detection
│   ├── main.py                  # FastAPI server & REST API endpoints
│   ├── requirements.txt         # Python dependencies
│   ├── test_yolo_api.py         # Portable automated API test script
│   ├── whatsapp_service.py      # WhatsApp Cloud API service integration
│   └── yolov8n.pt               # YOLOv8 nano model weights (~6.5 MB)
├── frontend/
│   ├── .env.example             # Frontend environment template
│   ├── package.json             # NPM package manifests & scripts
│   ├── package-lock.json        # NPM dependency lockfile
│   ├── vite.config.js           # Vite development & build configuration
│   ├── public/
│   │   └── models/face-api/     # Pre-trained Face-API model weights
│   └── src/
│       ├── App.jsx              # Main application root
│       ├── components/          # Reusable UI components
│       ├── context/             # SafetyContext global state
│       ├── services/            # API client, vision processors, face-api
│       └── views/               # Module screens (Dashboard, Radar, Alerts, etc.)
├── docs_and_reports/            # Workflow documentation and reports
├── .gitignore                   # Production Git ignore rules
└── README.md                    # Project documentation
```

---

## 🚀 Getting Started

### Prerequisites

- **Python:** 3.10 or higher
- **Node.js:** 18 or higher (tested on Node 20 / 24)
- **Git**

---

### Step 1: Clone the Repository

```bash
git clone https://github.com/Shiv-Kumar005023/IGL.git
cd IGL
```

---

### Step 2: Backend Setup (FastAPI)

1. Navigate to the backend directory:
   ```bash
   cd backend
   ```

2. (Optional) Create and activate a virtual environment:
   ```bash
   # Windows
   python -m venv venv
   venv\Scripts\activate

   # Linux / macOS
   python3 -m venv venv
   source venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Configure environment:
   ```bash
   # Copy template to .env
   cp .env.example .env     # Linux / macOS
   copy .env.example .env   # Windows
   ```

5. Initialize the database and launch the API server:
   ```bash
   python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
   ```

- Backend API: [http://127.0.0.1:8000](http://127.0.0.1:8000)
- Interactive API Documentation (Swagger): [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

---

### Step 3: Frontend Setup (React + Vite)

1. Navigate to the frontend directory:
   ```bash
   cd ../frontend
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Configure environment:
   ```bash
   cp .env.example .env     # Linux / macOS
   copy .env.example .env   # Windows
   ```
   *Note: In development, `VITE_API_BASE` defaults automatically to `http://127.0.0.1:8000/api`.*

4. Launch the frontend development server:
   ```bash
   npm run dev
   ```

- Frontend App: [http://localhost:3000](http://localhost:3000)

---

## 🧪 Testing & Verification

### Backend Verification

Run the automated YOLO detection test:
```bash
cd backend
python test_yolo_api.py
```

### Frontend Production Build

Test the production bundle compilation:
```bash
cd frontend
npm run build
```

---

## ⚙️ Environment Variables

### Backend (`backend/.env`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `ALLOWED_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Comma-separated CORS allowed origins |
| `DATABASE_PATH` | `igl_safety.db` | Path to SQLite database file |
| `YOLO_MODEL_PATH` | `yolov8n.pt` | Path or name of YOLO model |
| `WHATSAPP_MODE` | `mock` | `mock` for local simulation, `live` for Meta Cloud API |
| `WHATSAPP_ACCESS_TOKEN` | - | Meta WhatsApp Cloud API access token |
| `WHATSAPP_PHONE_NUMBER_ID` | - | Sender phone number ID from Meta portal |
| `CONTROLLER_WHATSAPP_NUMBER` | - | Recipient phone number for emergency alerts |

### Frontend (`frontend/.env`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `VITE_API_BASE` | `http://127.0.0.1:8000/api` | Base URL for FastAPI backend endpoints |

---

## 🌐 Production Deployment Guidelines

- **Frontend (Vercel / Netlify / Cloudflare Pages):**
  - Set Root Directory to `frontend`.
  - Set Build Command to `npm run build`.
  - Set Output Directory to `dist`.
  - Set Environment Variable `VITE_API_BASE` to your deployed backend URL (e.g. `https://api.yourdomain.com/api`).
- **Backend (Render / Railway / Fly.io / AWS / GCP):**
  - Use Python 3.10+ container runtime.
  - Set Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`.
  - Configure `ALLOWED_ORIGINS` with your frontend production URL.

---

## 📄 License

This project is licensed under the MIT License.
