import {defineConfig} from '@playwright/test';
import {randomUUID} from 'node:crypto';
export default defineConfig({testDir:'./tests/browser',fullyParallel:false,use:{baseURL:'http://127.0.0.1:5174',headless:true},reporter:'list',webServer:[
 {command:'.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8001',url:'http://127.0.0.1:8001/api/health',env:{DEMO_MODE:'true',AUTH_PROVIDER:'local',CORS_ORIGINS:'http://127.0.0.1:5174',DATABASE_PATH:`data/test-${randomUUID()}.db`},reuseExistingServer:false},
 {command:'npm run dev -- --port 5174 --strictPort',url:'http://127.0.0.1:5174',env:{VITE_BACKEND_URL:'http://127.0.0.1:8001'},reuseExistingServer:false}
]});
