using System.Collections.Generic;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.XR.ARFoundation;
using UnityEngine.XR.ARSubsystems;

namespace X3D.UnityBridge.XR
{
    /// <summary>Tap a detected plane to stand the X3D scene on it at real-world scale (X3D units are metres). Works through AR Foundation, so one component covers
    /// ARKit (iOS), ARCore (Android) and, with the matching provider, Quest passthrough / visionOS. Pinch to scale is left to the host app; scale is exposed.
    /// Compiles only when com.unity.xr.arfoundation >= 5 is installed (X3D_ARFOUNDATION), so the package never breaks a non-AR project.</summary>
    [RequireComponent(typeof(X3DPlayer))]
    public sealed class X3DARPlacer : MonoBehaviour
    {
        public ARRaycastManager raycastManager;
        public float sceneScale = 1f;
        public bool placed;
        static readonly List<ARRaycastHit> hits = new List<ARRaycastHit>();

        void Update()
        {
            if (raycastManager == null) return;
            Vector2 pos; if (!Tap(out pos)) return;
            if (raycastManager.Raycast(pos, hits, TrackableType.PlaneWithinPolygon))
            {
                var pose = hits[0].pose;
                transform.SetPositionAndRotation(pose.position, Quaternion.Euler(0, pose.rotation.eulerAngles.y, 0));
                transform.localScale = Vector3.one * sceneScale; placed = true;
            }
        }

        static bool Tap(out Vector2 p)
        {
            p = default;
            var ts = Touchscreen.current;
            if (ts != null && ts.primaryTouch.press.wasPressedThisFrame) { p = ts.primaryTouch.position.ReadValue(); return true; }
            var m = Mouse.current;                                  // editor / simulator
            if (m != null && m.leftButton.wasPressedThisFrame) { p = m.position.ReadValue(); return true; }
            return false;
        }
    }
}
