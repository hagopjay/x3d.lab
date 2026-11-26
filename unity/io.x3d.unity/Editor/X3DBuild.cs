using System;
using UnityEditor;
using UnityEngine;

namespace X3D.UnityBridge.Editor
{
    /// <summary>Headless builds for the long-run lane (his PC, or the Mac for iOS):
    ///   Unity -batchmode -quit -projectPath . -buildTarget Android -executeMethod X3D.UnityBridge.Editor.X3DBuild.Android -logFile build.log
    /// Scenes come from EditorBuildSettings. Output goes to ./Builds/&lt;target&gt;. iOS output is an Xcode project: open it on the Mac and sign there.</summary>
    public static class X3DBuild
    {
        public static void Android() { Run(BuildTarget.Android, "Builds/Android/x3d.apk"); }
        public static void IOS() { Run(BuildTarget.iOS, "Builds/iOS"); }
        public static void Windows() { Run(BuildTarget.StandaloneWindows64, "Builds/Win/x3d.exe"); }
        public static void WebGL() { Run(BuildTarget.WebGL, "Builds/WebGL"); }

        static void Run(BuildTarget target, string path)
        {
            var scenes = Array.ConvertAll(EditorBuildSettings.scenes, s => s.path);
            var rep = BuildPipeline.BuildPlayer(new BuildPlayerOptions { scenes = scenes, locationPathName = path, target = target, options = BuildOptions.None });
            Debug.Log("[X3D build] " + target + " " + rep.summary.result + " " + rep.summary.totalSize + " bytes, " + rep.summary.totalTime);
            EditorApplication.Exit(rep.summary.result == UnityEditor.Build.Reporting.BuildResult.Succeeded ? 0 : 1);
        }
    }
}
