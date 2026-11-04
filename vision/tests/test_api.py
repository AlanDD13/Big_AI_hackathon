from fastapi.testclient import TestClient
from vision.app import create_app
from vision.settings import Settings
from vision.classification import FixtureClassifier
from vision.tests.test_pipeline import jpeg, Detector


def client():
    return TestClient(create_app(Settings(token="test-token-long-enough"),
                      detector=Detector(), classifier=FixtureClassifier(), run_workers=False))


def headers():
    return {"Authorization":"Bearer test-token-long-enough", "X-Session-Id":"s1",
            "X-Geometry-Version":"g1", "X-Frame-Id":"0", "X-Captured-At":"1000",
            "Content-Type":"image/jpeg"}


def test_auth_and_camera_authorization():
    with client() as api:
        assert api.get("/health").status_code == 200
        assert api.get("/api/objects").status_code == 401
        assert api.post("/api/cameras/unknown/frames",headers=headers(),content=jpeg()).status_code == 404


def test_upload_is_immediate_and_frame_identity_is_exact():
    with client() as api:
        response = api.post("/api/cameras/phone-1/frames",headers=headers(),content=jpeg())
        assert response.status_code == 202
        assert response.json()["frameId"] == 0
        auth={"Authorization":headers()["Authorization"]}
        frame=api.get("/api/frames/phone-1/s1/0",headers=auth)
        assert frame.status_code==200 and frame.headers["content-type"]=="image/jpeg"
        assert api.get("/api/frames/phone-1/s1/1",headers=auth).status_code==404
        assert api.post("/api/cameras/phone-1/frames",headers=headers(),content=jpeg()).status_code==409


def test_invalid_images_and_metadata_rejected():
    with client() as api:
        assert api.post("/api/cameras/phone-1/frames",headers=headers(),content=b"not jpeg").status_code==422
        h=headers();h["X-Captured-At"]="nan"
        assert api.post("/api/cameras/phone-1/frames",headers=h,content=jpeg()).status_code==422


def test_phone_page_is_operational_source_not_dashboard():
    with client() as api:
        response=api.get("/camera")
        assert response.status_code==200
        assert "camera.js" in response.text
        assert "no-store" in response.headers["cache-control"]


def test_mapping_missing_identity_fails_validation():
    with client() as api:
        assert api.post("/api/mapping-evidence",headers={"Authorization":headers()["Authorization"]},json={}).status_code==422
