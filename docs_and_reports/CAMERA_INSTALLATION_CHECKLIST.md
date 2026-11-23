# IGL VisionGuard — Camera Installation Checklist

Use this checklist when an authorized factory CCTV camera becomes available.

## 1. Prepare

- [ ] Confirm the camera is authorized for the intended plant area.
- [ ] Confirm the backend host can reach the camera network and RTSP port.
- [ ] Obtain the RTSP URL and any required username/password.
- [ ] Decide the plant location and pipeline mapping.

## 2. Register

1. Open Camera Management (no sign-in required).
2. Open **Camera Management**.
3. Enter the camera name.
4. Select `RTSP` or `ONVIF / RTSP`.
5. Enter the authorized URL.
6. Enter credentials if required.
7. Click **Register camera**.
8. Click **Test** and verify a real preview.

If the test fails, verify:

- camera reachability from the backend host
- correct RTSP path and port
- credentials
- firewall rules
- camera resolution/encoding compatibility

## 3. Configure

1. Open **Camera Angle & Zones**.
2. Select the camera.
3. Start the camera.
4. Adjust the physical camera angle at installation.
5. Refresh the frame preview.
6. Set resolution and FPS.
7. Draw the pipeline monitoring polygon by clicking vertices on the preview.
8. Select the relevant AI models.
9. Save the zone.
10. Map the camera to a plant location and pipeline.
11. Save the camera configuration.

## 4. Verify

- [ ] Preview shows the real camera view.
- [ ] The monitoring polygon is on the pipeline.
- [ ] Camera status is `ONLINE`.
- [ ] Camera health shows no reconnect loop.
- [ ] A test detection/event can be generated with genuine input.
- [ ] Evidence is stored and accessible.
- [ ] Alerts are created with the correct camera/location/pipeline.
- [ ] Leakage incidents remain `POSSIBLE`/`SUSPECTED` until human review.

## 5. Start Monitoring

- [ ] Enable the camera.
- [ ] Confirm the event engine is processing frames.
- [ ] Confirm notification delivery status for the configured channels.
- [ ] Review and dismiss false positives to improve thresholds.

No new application development is required for ordinary camera onboarding.