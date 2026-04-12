# Bricks Dispatch - Product Requirements Document

## Overview
Bricks Dispatch is a mobile-first operations application for managing real estate media production jobs at scale. Used internally by photographers, editors, and dispatch coordinators to execute and track real estate media jobs.

## Tech Stack
- **Frontend**: React Native (Expo SDK 54) with Expo Router
- **Backend**: FastAPI (Python)
- **Database**: MongoDB (local)
- **Design**: Dark utilitarian theme (#050505 bg, #FACC15 accent)

## Features

### 1. Jobs Screen (Primary)
- List of assigned jobs with filter buttons (All, Assigned, In Progress, Completed)
- Job cards show: address, time window, job type (photo/video/drone/3d_tour/floor_plan), status, priority
- Priority indicators: URGENT (red), HIGH (yellow)
- Tap to navigate to job detail

### 2. Job Detail Screen
- Property details and client instructions
- Interactive checklist with progress bar
- Upload section for media files
- Status progression button: Assigned → In Progress → Completed
- Back navigation

### 3. Schedule Screen
- Weekly calendar view with day selector
- Timeline layout with visual connectors
- Week navigation (prev/next)
- Today indicator
- Tap job to view detail

### 4. Uploads Screen
- List of all uploaded files with metadata
- Upload button (image picker integration)
- Delete functionality
- File size and type display

### 5. Profile Screen
- User info (name, role, email, phone)
- Availability toggle (Online/Offline)
- Stats: Jobs completed, Rating, Member since
- Support and Sign Out actions

## API Endpoints
- `GET /api/jobs` - List jobs (optional ?status filter)
- `GET /api/jobs/{id}` - Job detail
- `PUT /api/jobs/{id}/status` - Update job status
- `PUT /api/jobs/{id}/checklist/{item_id}` - Toggle checklist item
- `GET /api/schedule?date=YYYY-MM-DD` - Schedule by date
- `GET /api/uploads` - List uploads
- `POST /api/uploads` - Create upload (FormData)
- `DELETE /api/uploads/{id}` - Delete upload
- `GET /api/profile` - Get profile
- `PUT /api/profile` - Update profile
- `POST /api/seed` - Re-seed demo data

## Seed Data
- 7 demo jobs across 3 days with various types and statuses
- 6 pre-seeded uploads for in-progress/completed jobs
- Default user profile (Alex Rivera, Lead Photographer)

## Design System
- **Inspired by**: CoStar CRM, BuildIn Construction, Petronex Oil & Gas SaaS dashboards (Rondesignlab Behance)
- Background: #0C0D14 (deep navy-dark)
- Surface: #151722 (dark navy)
- Border: #1E2030 (subtle navy borders)
- Primary Accent: #3B82F6 (vibrant blue)
- Status colors: Blue (Assigned), Orange/Yellow (In Progress), Green (Completed), Red (Urgent)
- Card radius: 14px (polished, rounded)
- Filter chips: pill-shaped (20px radius)
- Typography: clean, -0.5 letter-spacing headings, 500-700 weights
- Tab bar: blue active indicator dot, subtle active state
- No auth required
