"""
Backend API tests for Bricks Dispatch
Tests: Jobs, Schedule, Uploads, Profile endpoints
"""
import pytest
import requests
import os
from datetime import datetime
from pathlib import Path

# Read BASE_URL from frontend .env
def get_base_url():
    env_file = Path('/app/frontend/.env')
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                if line.startswith('EXPO_PUBLIC_BACKEND_URL='):
                    return line.split('=', 1)[1].strip()
    return ''

BASE_URL = get_base_url()

@pytest.fixture(scope="module")
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session

@pytest.fixture(scope="module")
def test_job_id(api_client):
    """Get a test job ID from seeded data"""
    response = api_client.get(f"{BASE_URL}/api/jobs")
    assert response.status_code == 200
    jobs = response.json()
    assert len(jobs) > 0, "No seeded jobs found"
    return jobs[0]['id']

@pytest.fixture(scope="module")
def assigned_job_id(api_client):
    """Get an assigned job ID for status transition tests"""
    response = api_client.get(f"{BASE_URL}/api/jobs?status=assigned")
    assert response.status_code == 200
    jobs = response.json()
    assert len(jobs) > 0, "No assigned jobs found"
    return jobs[0]['id']

@pytest.fixture(scope="module")
def in_progress_job_id(api_client):
    """Get an in_progress job ID for status transition tests"""
    response = api_client.get(f"{BASE_URL}/api/jobs?status=in_progress")
    assert response.status_code == 200
    jobs = response.json()
    assert len(jobs) > 0, "No in_progress jobs found"
    return jobs[0]['id']


class TestJobsEndpoints:
    """Test /api/jobs endpoints"""

    def test_get_all_jobs(self, api_client):
        """GET /api/jobs returns list of 7 seeded jobs"""
        response = api_client.get(f"{BASE_URL}/api/jobs")
        assert response.status_code == 200
        
        jobs = response.json()
        assert isinstance(jobs, list), "Response should be a list"
        assert len(jobs) == 7, f"Expected 7 seeded jobs, got {len(jobs)}"
        
        # Validate job structure
        job = jobs[0]
        required_fields = ['id', 'address', 'city', 'state', 'time_window_start', 
                          'time_window_end', 'job_type', 'status', 'priority', 
                          'scheduled_date', 'deliverables_count', 'uploaded_count', 'checklist']
        for field in required_fields:
            assert field in job, f"Job missing required field: {field}"

    def test_get_jobs_filter_assigned(self, api_client):
        """GET /api/jobs?status=assigned returns only assigned jobs"""
        response = api_client.get(f"{BASE_URL}/api/jobs?status=assigned")
        assert response.status_code == 200
        
        jobs = response.json()
        assert isinstance(jobs, list)
        assert len(jobs) > 0, "Should have at least one assigned job"
        
        # Verify all jobs have status 'assigned'
        for job in jobs:
            assert job['status'] == 'assigned', f"Job {job['id']} has status {job['status']}, expected 'assigned'"

    def test_get_jobs_filter_in_progress(self, api_client):
        """GET /api/jobs?status=in_progress returns only in_progress jobs"""
        response = api_client.get(f"{BASE_URL}/api/jobs?status=in_progress")
        assert response.status_code == 200
        
        jobs = response.json()
        assert isinstance(jobs, list)
        assert len(jobs) > 0, "Should have at least one in_progress job"
        
        for job in jobs:
            assert job['status'] == 'in_progress', f"Job {job['id']} has wrong status"

    def test_get_jobs_filter_completed(self, api_client):
        """GET /api/jobs?status=completed returns only completed jobs"""
        response = api_client.get(f"{BASE_URL}/api/jobs?status=completed")
        assert response.status_code == 200
        
        jobs = response.json()
        assert isinstance(jobs, list)
        assert len(jobs) > 0, "Should have at least one completed job"
        
        for job in jobs:
            assert job['status'] == 'completed', f"Job {job['id']} has wrong status"

    def test_get_job_by_id(self, api_client, test_job_id):
        """GET /api/jobs/{id} returns job detail with checklist"""
        response = api_client.get(f"{BASE_URL}/api/jobs/{test_job_id}")
        assert response.status_code == 200
        
        job = response.json()
        assert job['id'] == test_job_id
        assert 'checklist' in job
        assert isinstance(job['checklist'], list)
        
        # Validate checklist items structure
        if len(job['checklist']) > 0:
            item = job['checklist'][0]
            assert 'id' in item
            assert 'label' in item
            assert 'completed' in item
            assert isinstance(item['completed'], bool)

    def test_get_job_not_found(self, api_client):
        """GET /api/jobs/{id} returns 404 for non-existent job"""
        response = api_client.get(f"{BASE_URL}/api/jobs/nonexistent-job-id")
        assert response.status_code == 404


class TestJobStatusTransitions:
    """Test /api/jobs/{id}/status endpoint"""

    def test_status_transition_assigned_to_in_progress(self, api_client, assigned_job_id):
        """PUT /api/jobs/{id}/status transitions assigned->in_progress"""
        response = api_client.put(
            f"{BASE_URL}/api/jobs/{assigned_job_id}/status",
            json={"status": "in_progress"}
        )
        assert response.status_code == 200
        
        updated_job = response.json()
        assert updated_job['status'] == 'in_progress'
        assert updated_job['id'] == assigned_job_id
        
        # Verify persistence with GET
        get_response = api_client.get(f"{BASE_URL}/api/jobs/{assigned_job_id}")
        assert get_response.status_code == 200
        assert get_response.json()['status'] == 'in_progress'

    def test_status_transition_in_progress_to_completed(self, api_client, in_progress_job_id):
        """PUT /api/jobs/{id}/status transitions in_progress->completed"""
        response = api_client.put(
            f"{BASE_URL}/api/jobs/{in_progress_job_id}/status",
            json={"status": "completed"}
        )
        assert response.status_code == 200
        
        updated_job = response.json()
        assert updated_job['status'] == 'completed'
        
        # Verify persistence
        get_response = api_client.get(f"{BASE_URL}/api/jobs/{in_progress_job_id}")
        assert get_response.status_code == 200
        assert get_response.json()['status'] == 'completed'

    def test_status_transition_invalid_assigned_to_completed(self, api_client):
        """PUT /api/jobs/{id}/status rejects invalid transition assigned->completed"""
        # Get a fresh assigned job
        jobs_response = api_client.get(f"{BASE_URL}/api/jobs?status=assigned")
        jobs = jobs_response.json()
        
        if len(jobs) == 0:
            pytest.skip("No assigned jobs available for testing")
        
        job_id = jobs[0]['id']
        
        response = api_client.put(
            f"{BASE_URL}/api/jobs/{job_id}/status",
            json={"status": "completed"}
        )
        assert response.status_code == 400, "Should reject invalid transition"
        
        error = response.json()
        assert 'detail' in error
        assert 'Cannot transition' in error['detail']

    def test_status_transition_invalid_completed_to_assigned(self, api_client):
        """PUT /api/jobs/{id}/status rejects transition from completed"""
        # Get a completed job
        jobs_response = api_client.get(f"{BASE_URL}/api/jobs?status=completed")
        jobs = jobs_response.json()
        
        if len(jobs) == 0:
            pytest.skip("No completed jobs available for testing")
        
        job_id = jobs[0]['id']
        
        response = api_client.put(
            f"{BASE_URL}/api/jobs/{job_id}/status",
            json={"status": "assigned"}
        )
        assert response.status_code == 400, "Should reject transition from completed"


class TestChecklistToggle:
    """Test /api/jobs/{id}/checklist/{item_id} endpoint"""

    def test_toggle_checklist_item(self, api_client, test_job_id):
        """PUT /api/jobs/{id}/checklist/{item_id} toggles checklist items"""
        # Get job with checklist
        job_response = api_client.get(f"{BASE_URL}/api/jobs/{test_job_id}")
        job = job_response.json()
        
        if len(job['checklist']) == 0:
            pytest.skip("Job has no checklist items")
        
        item = job['checklist'][0]
        item_id = item['id']
        original_status = item['completed']
        
        # Toggle to opposite state
        response = api_client.put(
            f"{BASE_URL}/api/jobs/{test_job_id}/checklist/{item_id}",
            json={"completed": not original_status}
        )
        assert response.status_code == 200
        
        updated_job = response.json()
        updated_item = next(i for i in updated_job['checklist'] if i['id'] == item_id)
        assert updated_item['completed'] == (not original_status)
        
        # Verify persistence
        get_response = api_client.get(f"{BASE_URL}/api/jobs/{test_job_id}")
        persisted_job = get_response.json()
        persisted_item = next(i for i in persisted_job['checklist'] if i['id'] == item_id)
        assert persisted_item['completed'] == (not original_status)

    def test_toggle_checklist_item_not_found(self, api_client, test_job_id):
        """PUT /api/jobs/{id}/checklist/{item_id} returns 404 for non-existent item"""
        response = api_client.put(
            f"{BASE_URL}/api/jobs/{test_job_id}/checklist/nonexistent-item-id",
            json={"completed": True}
        )
        assert response.status_code == 404


class TestScheduleEndpoint:
    """Test /api/schedule endpoint"""

    def test_get_schedule_today(self, api_client):
        """GET /api/schedule?date=<today> returns today's jobs"""
        today = datetime.now().strftime("%Y-%m-%d")
        response = api_client.get(f"{BASE_URL}/api/schedule?date={today}")
        assert response.status_code == 200
        
        jobs = response.json()
        assert isinstance(jobs, list)
        
        # All jobs should have scheduled_date matching today
        for job in jobs:
            assert job['scheduled_date'] == today, f"Job {job['id']} has wrong date"

    def test_get_schedule_all(self, api_client):
        """GET /api/schedule without date returns all jobs"""
        response = api_client.get(f"{BASE_URL}/api/schedule")
        assert response.status_code == 200
        
        jobs = response.json()
        assert isinstance(jobs, list)
        assert len(jobs) == 7, "Should return all 7 seeded jobs"


class TestUploadsEndpoint:
    """Test /api/uploads endpoints"""

    def test_get_uploads(self, api_client):
        """GET /api/uploads returns uploads list"""
        response = api_client.get(f"{BASE_URL}/api/uploads")
        assert response.status_code == 200
        
        uploads = response.json()
        assert isinstance(uploads, list)
        
        # Validate upload structure
        if len(uploads) > 0:
            upload = uploads[0]
            required_fields = ['id', 'job_id', 'filename', 'file_size', 'mime_type', 'created_at']
            for field in required_fields:
                assert field in upload, f"Upload missing field: {field}"

    def test_get_uploads_by_job_id(self, api_client, test_job_id):
        """GET /api/uploads?job_id={id} filters by job"""
        response = api_client.get(f"{BASE_URL}/api/uploads?job_id={test_job_id}")
        assert response.status_code == 200
        
        uploads = response.json()
        assert isinstance(uploads, list)
        
        # All uploads should belong to the specified job
        for upload in uploads:
            assert upload['job_id'] == test_job_id

    def test_create_upload(self, api_client, test_job_id):
        """POST /api/uploads creates upload and increments job count"""
        # Get current uploaded count
        job_response = api_client.get(f"{BASE_URL}/api/jobs/{test_job_id}")
        job = job_response.json()
        initial_count = job['uploaded_count']
        
        # Create upload using multipart form data (data parameter, not files)
        form_data = {
            'job_id': test_job_id,
            'filename': 'TEST_upload.jpg',
            'file_size': 1024000,
            'mime_type': 'image/jpeg',
            'file_data': 'base64data'
        }
        
        # Remove Content-Type header to let requests set it for multipart
        headers = {}
        response = requests.post(f"{BASE_URL}/api/uploads", data=form_data, headers=headers)
        assert response.status_code == 200, f"Upload failed: {response.text}"
        
        upload = response.json()
        assert upload['job_id'] == test_job_id
        assert upload['filename'] == 'TEST_upload.jpg'
        assert upload['file_size'] == 1024000
        
        # Verify job uploaded_count incremented
        updated_job_response = api_client.get(f"{BASE_URL}/api/jobs/{test_job_id}")
        updated_job = updated_job_response.json()
        assert updated_job['uploaded_count'] == initial_count + 1

    def test_delete_upload(self, api_client, test_job_id):
        """DELETE /api/uploads/{id} deletes upload and decrements job count"""
        # Create an upload first
        form_data = {
            'job_id': test_job_id,
            'filename': 'TEST_delete_me.jpg',
            'file_size': 500000,
            'mime_type': 'image/jpeg',
            'file_data': ''
        }
        create_response = requests.post(f"{BASE_URL}/api/uploads", data=form_data)
        assert create_response.status_code == 200, f"Upload creation failed: {create_response.text}"
        upload = create_response.json()
        upload_id = upload['id']
        
        # Get current count
        job_response = api_client.get(f"{BASE_URL}/api/jobs/{test_job_id}")
        job = job_response.json()
        count_before_delete = job['uploaded_count']
        
        # Delete upload
        delete_response = api_client.delete(f"{BASE_URL}/api/uploads/{upload_id}")
        assert delete_response.status_code == 200
        assert delete_response.json()['deleted'] == True
        
        # Verify job count decremented
        updated_job_response = api_client.get(f"{BASE_URL}/api/jobs/{test_job_id}")
        updated_job = updated_job_response.json()
        assert updated_job['uploaded_count'] == count_before_delete - 1
        
        # Verify upload is gone
        get_response = api_client.get(f"{BASE_URL}/api/uploads/{upload_id}")
        # Upload should not be in the list anymore
        all_uploads = api_client.get(f"{BASE_URL}/api/uploads").json()
        upload_ids = [u['id'] for u in all_uploads]
        assert upload_id not in upload_ids


class TestProfileEndpoint:
    """Test /api/profile endpoints"""

    def test_get_profile(self, api_client):
        """GET /api/profile returns profile data"""
        response = api_client.get(f"{BASE_URL}/api/profile")
        assert response.status_code == 200
        
        profile = response.json()
        required_fields = ['id', 'name', 'email', 'role', 'phone', 'availability', 
                          'total_jobs_completed', 'rating', 'member_since']
        for field in required_fields:
            assert field in profile, f"Profile missing field: {field}"
        
        # Validate data types
        assert isinstance(profile['availability'], bool)
        assert isinstance(profile['total_jobs_completed'], int)
        assert isinstance(profile['rating'], (int, float))

    def test_update_profile_availability_toggle(self, api_client):
        """PUT /api/profile toggles availability"""
        # Get current availability
        get_response = api_client.get(f"{BASE_URL}/api/profile")
        profile = get_response.json()
        current_availability = profile['availability']
        
        # Toggle availability
        update_response = api_client.put(
            f"{BASE_URL}/api/profile",
            json={"availability": not current_availability}
        )
        assert update_response.status_code == 200
        
        updated_profile = update_response.json()
        assert updated_profile['availability'] == (not current_availability)
        
        # Verify persistence
        verify_response = api_client.get(f"{BASE_URL}/api/profile")
        verified_profile = verify_response.json()
        assert verified_profile['availability'] == (not current_availability)
        
        # Toggle back to original state
        restore_response = api_client.put(
            f"{BASE_URL}/api/profile",
            json={"availability": current_availability}
        )
        assert restore_response.status_code == 200

    def test_update_profile_name(self, api_client):
        """PUT /api/profile updates name"""
        # Get current profile
        get_response = api_client.get(f"{BASE_URL}/api/profile")
        original_profile = get_response.json()
        original_name = original_profile['name']
        
        # Update name
        test_name = "TEST_Updated Name"
        update_response = api_client.put(
            f"{BASE_URL}/api/profile",
            json={"name": test_name}
        )
        assert update_response.status_code == 200
        assert update_response.json()['name'] == test_name
        
        # Restore original name
        api_client.put(f"{BASE_URL}/api/profile", json={"name": original_name})



class TestNotificationEndpoints:
    """Test /api/notifications endpoints - NEW FEATURE"""

    def test_get_notifications(self, api_client):
        """GET /api/notifications returns notification list"""
        response = api_client.get(f"{BASE_URL}/api/notifications")
        assert response.status_code == 200
        
        notifications = response.json()
        assert isinstance(notifications, list), "Response should be a list"
        
        # Validate notification structure
        if len(notifications) > 0:
            notif = notifications[0]
            required_fields = ['id', 'title', 'body', 'type', 'data', 'read', 'created_at']
            for field in required_fields:
                assert field in notif, f"Notification missing field: {field}"
            
            # Validate data types
            assert isinstance(notif['read'], bool)
            assert isinstance(notif['data'], dict)
            assert notif['type'] in ['info', 'new_job', 'status_change', 'reminder', 'upload']

    def test_get_unread_count(self, api_client):
        """GET /api/notifications/unread-count returns count"""
        response = api_client.get(f"{BASE_URL}/api/notifications/unread-count")
        assert response.status_code == 200
        
        data = response.json()
        assert 'count' in data, "Response should have 'count' field"
        assert isinstance(data['count'], int), "Count should be an integer"
        assert data['count'] >= 0, "Count should be non-negative"

    def test_mark_notification_read(self, api_client):
        """PUT /api/notifications/{id}/read marks notification as read"""
        # Get notifications to find an unread one
        notifs_response = api_client.get(f"{BASE_URL}/api/notifications")
        notifs = notifs_response.json()
        
        if len(notifs) == 0:
            pytest.skip("No notifications available for testing")
        
        # Find an unread notification or use the first one
        test_notif = next((n for n in notifs if not n['read']), notifs[0])
        notif_id = test_notif['id']
        
        # Get initial unread count
        count_before = api_client.get(f"{BASE_URL}/api/notifications/unread-count").json()['count']
        
        # Mark as read
        response = api_client.put(f"{BASE_URL}/api/notifications/{notif_id}/read")
        assert response.status_code == 200
        assert response.json()['read'] == True
        
        # Verify notification is marked as read
        updated_notifs = api_client.get(f"{BASE_URL}/api/notifications").json()
        updated_notif = next((n for n in updated_notifs if n['id'] == notif_id), None)
        assert updated_notif is not None
        assert updated_notif['read'] == True
        
        # Verify unread count decreased (if it was unread before)
        if not test_notif['read']:
            count_after = api_client.get(f"{BASE_URL}/api/notifications/unread-count").json()['count']
            assert count_after == count_before - 1


class TestPushTokenEndpoint:
    """Test /api/push-tokens endpoint - NEW FEATURE"""

    def test_register_push_token(self, api_client):
        """POST /api/push-tokens registers a new push token"""
        token_data = {
            "token": "ExponentPushToken[TEST_xxxxxxxxxxxxxxxxxxxxxx]",
            "platform": "ios"
        }
        
        response = api_client.post(f"{BASE_URL}/api/push-tokens", json=token_data)
        assert response.status_code == 200
        
        result = response.json()
        assert 'registered' in result
        assert result['registered'] == True

    def test_register_push_token_duplicate(self, api_client):
        """POST /api/push-tokens with duplicate token updates existing"""
        token_data = {
            "token": "ExponentPushToken[TEST_duplicate_token_12345]",
            "platform": "android"
        }
        
        # Register first time
        response1 = api_client.post(f"{BASE_URL}/api/push-tokens", json=token_data)
        assert response1.status_code == 200
        assert response1.json()['registered'] == True
        
        # Register again with same token (should update, not create new)
        response2 = api_client.post(f"{BASE_URL}/api/push-tokens", json=token_data)
        assert response2.status_code == 200
        assert response2.json()['registered'] == True


class TestNotificationTriggers:
    """Test notification creation triggers - NEW FEATURE"""

    def test_status_change_creates_notification(self, api_client):
        """PUT /api/jobs/{id}/status creates a notification"""
        # Get an assigned job
        jobs_response = api_client.get(f"{BASE_URL}/api/jobs?status=assigned")
        jobs = jobs_response.json()
        
        if len(jobs) == 0:
            pytest.skip("No assigned jobs available for testing")
        
        job_id = jobs[0]['id']
        
        # Get initial notification count
        notifs_before = api_client.get(f"{BASE_URL}/api/notifications").json()
        count_before = len(notifs_before)
        
        # Change job status
        status_response = api_client.put(
            f"{BASE_URL}/api/jobs/{job_id}/status",
            json={"status": "in_progress"}
        )
        assert status_response.status_code == 200
        
        # Verify notification was created
        notifs_after = api_client.get(f"{BASE_URL}/api/notifications").json()
        count_after = len(notifs_after)
        assert count_after == count_before + 1, "Status change should create a notification"
        
        # Verify notification content
        new_notif = notifs_after[0]  # Most recent notification (sorted by created_at desc)
        assert new_notif['type'] == 'status_change'
        assert 'job_id' in new_notif['data']
        assert new_notif['data']['job_id'] == job_id
        assert new_notif['data']['status'] == 'in_progress'

    def test_upload_creates_notification(self, api_client):
        """POST /api/uploads creates a notification"""
        # Get a job for upload
        jobs_response = api_client.get(f"{BASE_URL}/api/jobs")
        jobs = jobs_response.json()
        
        if len(jobs) == 0:
            pytest.skip("No jobs available for testing")
        
        job_id = jobs[0]['id']
        
        # Get initial notification count
        notifs_before = api_client.get(f"{BASE_URL}/api/notifications").json()
        count_before = len(notifs_before)
        
        # Create upload
        form_data = {
            'job_id': job_id,
            'filename': 'TEST_notification_upload.jpg',
            'file_size': 2048000,
            'mime_type': 'image/jpeg',
            'file_data': 'base64test'
        }
        
        upload_response = requests.post(f"{BASE_URL}/api/uploads", data=form_data)
        assert upload_response.status_code == 200
        
        # Verify notification was created
        notifs_after = api_client.get(f"{BASE_URL}/api/notifications").json()
        count_after = len(notifs_after)
        assert count_after == count_before + 1, "Upload should create a notification"
        
        # Verify notification content
        new_notif = notifs_after[0]  # Most recent notification
        assert new_notif['type'] == 'upload'
        assert 'job_id' in new_notif['data']
        assert new_notif['data']['job_id'] == job_id
        assert 'filename' in new_notif['data']
        assert new_notif['data']['filename'] == 'TEST_notification_upload.jpg'
