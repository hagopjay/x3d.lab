using System;
using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

namespace X3D.UnityBridge
{
    /// <summary>x3d-live/v1 client. Polls GET {server}/manifest.json with If-None-Match; on a new etag, fetches /scene.x3d and rebuilds.
    /// Plain HTTP polling on purpose: it works the same on iOS, Android, Quest and visionOS with no socket plugin, and survives headset sleep.
    /// The server only ever publishes scenes that passed the x3d_mcp XSD + semantic gate (see tools/live_server.mjs).</summary>
    [AddComponentMenu("X3D/X3D Live Client")]
    public sealed class X3DLiveClient : MonoBehaviour
    {
        public X3DPlayer player;
        [Tooltip("e.g. http://192.168.1.20:8787 (LAN) or an https tunnel")] public string server = "http://127.0.0.1:8787";
        public float pollSeconds = 1.0f;
        public string status = "idle";
        string etag = "";

        void OnEnable() { StartCoroutine(Loop()); }

        IEnumerator Loop()
        {
            var wait = new WaitForSeconds(pollSeconds);
            while (true)
            {
                using (var req = UnityWebRequest.Get(server.TrimEnd('/') + "/manifest.json"))
                {
                    if (etag.Length > 0) req.SetRequestHeader("If-None-Match", etag);
                    req.timeout = 5;
                    yield return req.SendWebRequest();
                    if (req.responseCode == 200)
                    {
                        var newTag = req.GetResponseHeader("ETag") ?? "";
                        using (var sc = UnityWebRequest.Get(server.TrimEnd('/') + "/scene.x3d"))
                        {
                            yield return sc.SendWebRequest();
                            if (sc.result == UnityWebRequest.Result.Success)
                            {
                                try { player.Load(sc.downloadHandler.text); etag = newTag; status = "live " + newTag; }
                                catch (Exception e) { status = "build failed: " + e.Message; Debug.LogError("[X3D live] " + e); etag = newTag; }
                            }
                            else status = "scene fetch " + sc.error;
                        }
                    }
                    else if (req.responseCode != 304) status = "manifest " + (req.error ?? req.responseCode.ToString());
                }
                yield return wait;
            }
        }
    }
}
