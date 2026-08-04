# Phase 3 Planning: Portability & Deployment

**Status:** 📋 PLANNED (Ready to deploy after Phase 2)  
**Total Effort:** 22 hours  
**Timeline:** 2-3 weeks

---

## Overview

Phase 3 enables multi-environment deployment and team collaboration across platforms.

**Outcomes:**
- ✅ Docker support (scheduler, dashboard, orchestrator)
- ✅ Cross-platform scripts (Linux, Mac, Windows)
- ✅ Cloud deployment configs (Heroku, systemd, Kubernetes)

---

## 3.1: Docker Support (8 hours)

### What It Does
Containerizes the application for easy deployment across environments.

### Deliverables

**Dockerfile.scheduler** — Background DTCC polling service
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
CMD ["python", "scheduled_ingest.py"]
```

**Dockerfile.dashboard** — FastAPI web interface
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "dashboard.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

**docker-compose.yml** — Local development stack
```yaml
version: '3.8'
services:
  scheduler:
    build:
      context: .
      dockerfile: Dockerfile.scheduler
    environment:
      - DASHBOARD_API_KEY=${DASHBOARD_API_KEY}
      - THETADATA_CF_ACCESS_CLIENT_ID=${THETADATA_CF_ACCESS_CLIENT_ID}
      - THETADATA_CF_ACCESS_CLIENT_SECRET=${THETADATA_CF_ACCESS_CLIENT_SECRET}
    volumes:
      - ./swaps.db:/app/swaps.db
      - ./.env:/app/.env

  dashboard:
    build:
      context: .
      dockerfile: Dockerfile.dashboard
    ports:
      - "8787:8000"
    environment:
      - DASHBOARD_API_KEY=${DASHBOARD_API_KEY}
      - THETADATA_CF_ACCESS_CLIENT_ID=${THETADATA_CF_ACCESS_CLIENT_ID}
      - THETADATA_CF_ACCESS_CLIENT_SECRET=${THETADATA_CF_ACCESS_CLIENT_SECRET}
    volumes:
      - ./swaps.db:/app/swaps.db
      - ./.env:/app/.env
```

### Files to Create
- `Dockerfile.scheduler`
- `Dockerfile.dashboard`
- `docker-compose.yml`
- `.dockerignore`

### Testing
```bash
docker-compose up
# Dashboard: http://localhost:8787
# Verify scheduler running: docker logs financialdevelopment_scheduler_1
```

---

## 3.2: Cross-Platform Scripts (6 hours)

### What It Does
Enables running on Linux/Mac in addition to Windows.

### Deliverables

**orchestrator.sh** (Linux/Mac equivalent of orchestrator.bat)
```bash
#!/bin/bash
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PYTHON="${SCRIPT_DIR}/.venv/bin/python"
"$PYTHON" "${SCRIPT_DIR}/orchestrator.py" "$@"
```

**run_scheduler.sh** (Linux/Mac equivalent of run_scheduler.bat)
```bash
#!/bin/bash
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PYTHON="${SCRIPT_DIR}/.venv/bin/python"
"$PYTHON" "${SCRIPT_DIR}/scheduled_ingest.py"
```

**dashboard.sh** (Linux/Mac equivalent of dashboard.bat)
```bash
#!/bin/bash
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PYTHON="${SCRIPT_DIR}/.venv/bin/python"
"$PYTHON" -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8000
```

### Files to Create
- `orchestrator.sh`
- `run_scheduler.sh`
- `dashboard.sh`
- `.gitignore` updates (to exclude platform-specific files)

### Updates to Python Files
- `orchestrator.py` — Use `pathlib.Path` instead of hardcoded Windows paths
- All imports — Remove hardcoded `.venv\Scripts\python.exe`

### Testing
```bash
# On Linux/Mac
bash orchestrator.sh --unified --ticker NVDA --target-years 0.25
bash run_scheduler.sh
bash dashboard.sh
```

---

## 3.3: Cloud Deployment Configs (8 hours)

### What It Does
Enables deployment to cloud platforms (Heroku, AWS, Kubernetes, systemd).

### Deliverables

**Procfile** (Heroku deployment)
```
web: python -m uvicorn dashboard.app:app --host 0.0.0.0 --port $PORT
scheduler: python scheduled_ingest.py
```

**system/financialdevelopment-scheduler.service** (systemd service)
```ini
[Unit]
Description=FinancialDevelopment DTCC Scheduler
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/financialdevelopment
ExecStart=/opt/financialdevelopment/.venv/bin/python scheduled_ingest.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**system/financialdevelopment-dashboard.service** (systemd service)
```ini
[Unit]
Description=FinancialDevelopment Dashboard
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/financialdevelopment
ExecStart=/opt/financialdevelopment/.venv/bin/python -m uvicorn dashboard.app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**k8s/deployment.yaml** (Kubernetes)
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: financialdevelopment-dashboard
spec:
  replicas: 2
  selector:
    matchLabels:
      app: dashboard
  template:
    metadata:
      labels:
        app: dashboard
    spec:
      containers:
      - name: dashboard
        image: financialdevelopment:latest
        ports:
        - containerPort: 8000
        env:
        - name: DASHBOARD_API_KEY
          valueFrom:
            secretKeyRef:
              name: financialdevelopment-secrets
              key: api-key
        volumeMounts:
        - name: swaps-db
          mountPath: /app/swaps.db
      volumes:
      - name: swaps-db
        persistentVolumeClaim:
          claimName: swaps-db-pvc
```

**DEPLOY.md** — Deployment guide
```markdown
# Deployment Guide

## Heroku
```bash
heroku create financialdevelopment
git push heroku main
heroku config:set DASHBOARD_API_KEY=your-key-here
```

## Systemd (Linux)
```bash
sudo cp system/*.service /etc/systemd/system/
sudo systemctl enable financialdevelopment-scheduler
sudo systemctl enable financialdevelopment-dashboard
sudo systemctl start financialdevelopment-scheduler
sudo systemctl start financialdevelopment-dashboard
```

## Kubernetes
```bash
kubectl create secret generic financialdevelopment-secrets \
  --from-literal=api-key=your-key-here
kubectl apply -f k8s/
```
```

### Files to Create
- `Procfile`
- `system/financialdevelopment-scheduler.service`
- `system/financialdevelopment-dashboard.service`
- `k8s/deployment.yaml`
- `k8s/service.yaml`
- `DEPLOY.md`

### Testing
```bash
# Heroku
heroku local  # Run locally as if on Heroku

# Systemd
sudo systemctl status financialdevelopment-scheduler
sudo journalctl -u financialdevelopment-scheduler -f

# Kubernetes
kubectl get pods
kubectl logs -f deployment/financialdevelopment-dashboard
```

---

## Implementation Strategy

### Sequential Steps
1. **Week 1:** Docker support (8h)
2. **Week 2:** Cross-platform scripts (6h)
3. **Week 3:** Cloud deployment (8h)

### Parallel Opportunities
- Docker and cross-platform scripts can overlap
- Cloud configs depend on Docker, so sequence matters

### Testing Per Sub-Phase
- After Docker: `docker-compose up` and verify both services
- After Scripts: Test on Linux/Mac VMs
- After Cloud: Deploy test instance to each target platform

---

## Acceptance Criteria

✅ **Docker:**
- Both services build and run in docker-compose
- Volume mounts work (swaps.db persisted)
- Environment variables passed correctly

✅ **Cross-Platform:**
- `.sh` scripts work on Ubuntu 20.04+, macOS 11+
- `pathlib.Path` used throughout (no hardcoded paths)
- No Windows-specific dependencies

✅ **Cloud:**
- Heroku: Deploy and verify dashboard accessible
- Systemd: Services auto-restart on failure
- Kubernetes: Pod scaling works, PVC mounts correctly

---

## Success Metrics

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Docker image size | < 500MB | `docker images` |
| Container startup | < 30s | Time from `docker-compose up` to dashboard responsive |
| Cross-platform test pass rate | 100% | Run test suite on Linux/Mac |
| Deployment time (Heroku) | < 5 min | Time from `git push heroku main` to live |

---

## Dependencies on Earlier Phases

**Phase 1:** ✅ API key auth (required for dashboard deployment)  
**Phase 2:** ✅ Schema migrations (required for cloud-scale deployments)  
**Phase 3:** 📋 Portability (enables multi-environment)  
**Phase 4:** Requires Phase 3 (multi-source in cloud context)  
**Phase 5:** Requires Phase 3 (observability across environments)

---

## Post-Phase-3 Status

After Phase 3 completion:
- ✅ Docker: Deploy anywhere with one command
- ✅ Linux/Mac: Full parity with Windows
- ✅ Cloud: Ready for staging/production deployment
- ✅ Team Collaboration: Easy onboarding (any OS, any cloud)

**Ready for:** Phase 4 (Multi-Source Architecture) and Phase 5 (Observability & Scaling)

