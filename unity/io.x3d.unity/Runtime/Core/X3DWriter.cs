using System.Text;
using System.Security;

namespace X3D.Core
{
    /// <summary>Scene model back to XML X3D (used by the Unity exporter and by the round-trip test).</summary>
    public static class X3DWriter
    {
        public static string Write(X3DScene s)
        {
            var sb = new StringBuilder();
            sb.Append("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n");
            sb.Append("<X3D profile=\"" + s.Profile + "\" version=\"" + s.Version + "\">\n  <Scene>\n");
            foreach (var n in s.Roots) Node(sb, n, 2);
            foreach (var r in s.Routes)
                sb.Append("    <ROUTE fromNode=\"" + Esc(r.FromNode) + "\" fromField=\"" + Esc(r.FromField) + "\" toNode=\"" + Esc(r.ToNode) + "\" toField=\"" + Esc(r.ToField) + "\"/>\n");
            sb.Append("  </Scene>\n</X3D>\n");
            return sb.ToString();
        }

        static void Node(StringBuilder sb, X3DNode n, int d)
        {
            var pad = new string(' ', d * 2);
            sb.Append(pad + "<" + n.Type);
            if (n.UseName != null) { sb.Append(" USE=\"" + Esc(n.UseName) + "\"/>\n"); return; }
            if (n.Def != null) sb.Append(" DEF=\"" + Esc(n.Def) + "\"");
            if (n.ContainerField != X3DNode.DefaultContainerField(n.Type)) sb.Append(" containerField=\"" + Esc(n.ContainerField) + "\"");
            foreach (var kv in n.Fields) sb.Append(" " + kv.Key + "=\"" + Esc(kv.Value) + "\"");
            if (n.Children.Count == 0) { sb.Append("/>\n"); return; }
            sb.Append(">\n");
            foreach (var c in n.Children) Node(sb, c, d + 1);
            sb.Append(pad + "</" + n.Type + ">\n");
        }

        static string Esc(string v) { return SecurityElement.Escape(v) ?? ""; }
    }
}
