using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

// Lanzador de Jarvis.exe: ubica el interprete de Python del venv del
// proyecto (junto a este ejecutable) y arranca "python -m jarvis.main" una
// sola vez, sin ventana de consola. Si no encuentra el venv o algo falla al
// arrancar, muestra un mensaje claro en vez de fallar en silencio.
//
// La salida (stdout/stderr) se redirige a logs\launcher.log en vez de
// perderse (como pasaria con pythonw.exe sin consola): si algo revienta
// dentro de jarvis.main (por ejemplo el microfono no disponible), queda
// registrado y se puede diagnosticar. Se hace vía "cmd /c ... > log 2>&1"
// en lugar de leer los pipes desde este proceso, porque este lanzador
// termina apenas arranca Jarvis y no se queda vivo para vaciar los pipes.
internal static class Launcher
{
    [STAThread]
    private static void Main()
    {
        string projectDir = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(projectDir, "venv", "Scripts", "python.exe");

        if (!File.Exists(python))
        {
            MessageBox.Show(
                "No se encontro el interprete de Python del entorno virtual.\n\n" +
                "Verifica que la carpeta 'venv' exista junto a Jarvis.exe " +
                "(ver requirements.txt para recrearla si hace falta).",
                "Jarvis - Error al iniciar",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return;
        }

        string logDir = Path.Combine(projectDir, "logs");
        Directory.CreateDirectory(logDir);
        string logFile = Path.Combine(logDir, "launcher.log");

        // Si un proceso huerfano de una sesion anterior (p.ej. un
        // chromedriver.exe que se quedo vivo y heredo el handle de este
        // archivo) todavia tiene launcher.log abierto, el ">" del cmd de
        // abajo fallaria al abrirlo para escritura y toda la cadena
        // (cmd -> python) truena en silencio sin dejar rastro, sin mensaje
        // de error ni log. Se intenta borrar primero; si esta bloqueado,
        // se usa un nombre con marca de tiempo en vez de bloquear el
        // arranque de Jarvis por un log viejo.
        try
        {
            if (File.Exists(logFile))
            {
                File.Delete(logFile);
            }
        }
        catch (IOException)
        {
            string stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
            logFile = Path.Combine(logDir, "launcher_" + stamp + ".log");
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = "cmd.exe",
            Arguments = string.Format(
                "/c \"\"{0}\" -u -m jarvis.main > \"{1}\" 2>&1\"",
                python, logFile),
            WorkingDirectory = projectDir,
            UseShellExecute = false,
            CreateNoWindow = true,
        };

        try
        {
            Process.Start(startInfo);
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                "No se pudo iniciar Jarvis:\n\n" + ex.Message,
                "Jarvis - Error al iniciar",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }
    }
}
