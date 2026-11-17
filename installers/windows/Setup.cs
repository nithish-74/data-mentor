// Data Architect Mentor - Windows installer / uninstaller (per user, no admin rights).
// Build: installers\build_windows.ps1   (compiled with the C# compiler that ships with Windows)
//
// Setup.exe              -> asks, installs, opens the app
// Setup.exe /uninstall   -> removes shortcuts, app folder and the Settings > Apps entry
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;
using Microsoft.Win32;

static class Setup
{
    const string AppName = "Data Architect Mentor";
    const string AppUrl = "__APP_URL__";
    const string KeyPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\DataArchitectMentor";

    static string Dest { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\DataArchitectMentor"); } }
    static string[] Links
    {
        get
        {
            return new[] {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), AppName + ".lnk"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), AppName + ".lnk") };
        }
    }

    [STAThread]
    static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        try
        {
            if (args.Length > 0 && args[0].Trim('-', '/').Equals("uninstall", StringComparison.OrdinalIgnoreCase))
                return Uninstall();
            return Install();
        }
        catch (Exception e)
        {
            MessageBox.Show("Something went wrong: " + e.Message, AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    static string FindBrowser()
    {
        foreach (var exe in new[] { "msedge.exe", "chrome.exe" })
            foreach (var hive in new[] { Registry.LocalMachine, Registry.CurrentUser })
            {
                using (var k = hive.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\" + exe))
                {
                    var p = k == null ? null : k.GetValue("") as string;
                    if (!string.IsNullOrEmpty(p) && File.Exists(p)) return p;
                }
            }
        return null;
    }

    static int Install()
    {
        var answer = MessageBox.Show(
            "Install " + AppName + " on this PC?\n\nIt opens in its own window and can send you a daily study reminder.",
            AppName, MessageBoxButtons.OKCancel, MessageBoxIcon.Question);
        if (answer != DialogResult.OK) return 0;

        var browser = FindBrowser();
        if (browser == null)
        {
            MessageBox.Show("Microsoft Edge or Google Chrome is needed. Install one of them and run Setup again.", AppName);
            return 1;
        }

        Directory.CreateDirectory(Dest);
        var self = Assembly.GetExecutingAssembly().Location;
        var installed = Path.Combine(Dest, "DataArchitectMentor.exe");
        if (!string.Equals(Path.GetFullPath(self), Path.GetFullPath(installed), StringComparison.OrdinalIgnoreCase))
            File.Copy(self, installed, true);   // the same exe is the icon source and the uninstaller

        var shellType = Type.GetTypeFromProgID("WScript.Shell");
        dynamic shell = Activator.CreateInstance(shellType);
        foreach (var lnk in Links)
        {
            dynamic s = shell.CreateShortcut(lnk);
            s.TargetPath = browser;
            s.Arguments = "--app=" + AppUrl;
            s.IconLocation = installed + ",0";
            s.Description = "Learn data modeling and architecture, one topic a day";
            s.WorkingDirectory = Dest;
            s.Save();
        }

        using (var k = Registry.CurrentUser.CreateSubKey(KeyPath))
        {
            k.SetValue("DisplayName", AppName);
            k.SetValue("DisplayIcon", installed + ",0");
            k.SetValue("Publisher", AppName);
            k.SetValue("DisplayVersion", "1.0");
            k.SetValue("InstallLocation", Dest);
            k.SetValue("NoModify", 1, RegistryValueKind.DWord);
            k.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            k.SetValue("UninstallString", "\"" + installed + "\" /uninstall");
        }

        Process.Start(browser, "--app=" + AppUrl);
        MessageBox.Show(AppName + " is installed.\n\nOpen it any time from the Start menu or your desktop. " +
                        "Sign in with your username, then turn on notifications on the You page.",
                        AppName, MessageBoxButtons.OK, MessageBoxIcon.Information);
        return 0;
    }

    static int Uninstall()
    {
        var answer = MessageBox.Show("Remove " + AppName + " from this PC?\n\nYour progress stays on the mentor site.",
                                     AppName, MessageBoxButtons.OKCancel, MessageBoxIcon.Question);
        if (answer != DialogResult.OK) return 0;
        foreach (var lnk in Links) if (File.Exists(lnk)) File.Delete(lnk);
        Registry.CurrentUser.DeleteSubKeyTree(KeyPath, false);
        // the running exe can't delete itself: remove the folder a moment after we exit
        Process.Start(new ProcessStartInfo("cmd.exe", "/c timeout /t 2 >nul & rmdir /s /q \"" + Dest + "\"")
        { CreateNoWindow = true, UseShellExecute = false });
        MessageBox.Show(AppName + " has been removed.", AppName, MessageBoxButtons.OK, MessageBoxIcon.Information);
        return 0;
    }
}
