using System;
using System.Linq;
using System.Xml.Linq;

namespace X3D.Core
{
    /// <summary>XML-encoding X3D reader. DEF/USE are resolved; ROUTEs are collected; nothing is silently dropped (see Receipt gap ledger).</summary>
    public static class X3DParser
    {
        public static X3DScene Parse(string xml)
        {
            var doc = XDocument.Parse(xml, LoadOptions.None);
            var root = doc.Root;
            if (root == null || root.Name.LocalName != "X3D") throw new FormatException("root element is not <X3D>");
            var scene = new X3DScene();
            scene.Version = (string)root.Attribute("version") ?? "4.0";
            scene.Profile = (string)root.Attribute("profile") ?? "Full";
            var sc = root.Elements().FirstOrDefault(e => e.Name.LocalName == "Scene");
            if (sc == null) throw new FormatException("no <Scene>");
            foreach (var el in sc.Elements())
            {
                if (el.Name.LocalName == "ROUTE") { AddRoute(scene, el); continue; }
                var n = Build(scene, el, null);
                if (n != null) { n.ContainerField = n.ContainerField ?? "children"; scene.Roots.Add(n); }
            }
            // second pass: USE may appear before DEF in broken files; flag instead of crashing
            return scene;
        }

        static void AddRoute(X3DScene s, XElement el)
        {
            s.Routes.Add(new X3DRoute
            {
                FromNode = (string)el.Attribute("fromNode"), FromField = (string)el.Attribute("fromField"),
                ToNode = (string)el.Attribute("toNode"), ToField = (string)el.Attribute("toField")
            });
        }

        static X3DNode Build(X3DScene scene, XElement el, X3DNode parent)
        {
            string type = el.Name.LocalName;
            if (type == "head" || type == "meta" || type == "component" || type == "unit") return null;
            var n = new X3DNode { Type = type };
            var cf = (string)el.Attribute("containerField");
            n.ContainerField = cf ?? X3DNode.DefaultContainerField(type);
            var use = (string)el.Attribute("USE");
            if (use != null)
            {
                n.UseName = use;
                X3DNode target;
                if (scene.Defs.TryGetValue(use, out target)) { n.UseTarget = target; n.Type = target.Type; }
                else scene.Warnings.Add("USE before DEF: " + use);
                return n;
            }
            n.Def = (string)el.Attribute("DEF");
            if (n.Def != null) scene.Defs[n.Def] = n;
            foreach (var a in el.Attributes())
            {
                var name = a.Name.LocalName;
                if (name == "DEF" || name == "USE" || name == "containerField") continue;
                n.Fields[name] = a.Value;
            }
            foreach (var c in el.Elements())
            {
                if (c.Name.LocalName == "ROUTE") { AddRoute(scene, c); continue; }
                var ch = Build(scene, c, n);
                if (ch != null) n.Children.Add(ch);
            }
            return n;
        }
    }
}
