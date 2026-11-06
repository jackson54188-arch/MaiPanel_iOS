// Reads the 1P RGB LED state out of maimai DX and streams it to the bridge.
// The game computes LED colours inside Mecha.Bd15070_4IF; these are the same
// private methods AquaMai hooks for Maimoller hardware, so the signatures are
// proven against SDEZ 1.60.

using System;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Threading;
using HarmonyLib;
using MelonLoader;
using Mecha;
using UnityEngine;

[assembly: MelonInfo(typeof(MaiPanelLed.MaiPanelLedMod), "MaiPanelLed", "0.1.0", "MaiPanel")]
[assembly: MelonGame(null, null)]

namespace MaiPanelLed
{
    public sealed class MaiPanelLedMod : MelonMod
    {
        private const int Port = 24872;
        private HarmonyLib.Harmony harmony;
        private Thread thread;
        private volatile bool running = true;
        private UdpClient udp;
        private IPEndPoint target;

        public override void OnInitializeMelon()
        {
            LedState.Reset();
            harmony = new HarmonyLib.Harmony("maipanel.led");
            harmony.PatchAll(Assembly.GetExecutingAssembly());
            target = new IPEndPoint(IPAddress.Loopback, Port);
            udp = new UdpClient();
            thread = new Thread(SenderLoop);
            thread.IsBackground = true;
            thread.Start();
            LoggerInstance.Msg("LED 已启动 -> 127.0.0.1:" + Port);
        }

        public override void OnApplicationQuit()
        {
            running = false;
            if (thread != null) thread.Join(200);
            if (udp != null) udp.Close();
            if (harmony != null) harmony.UnpatchSelf();
        }

        private void SenderLoop()
        {
            long lastSend = 0;
            while (running)
            {
                long now = LedState.Now;
                LedState.Tick(now);
                if (LedState.NeedsSend(now, lastSend))
                {
                    byte[] packet = LedState.BuildPacket();
                    try
                    {
                        udp.Send(packet, packet.Length, target);
                        lastSend = now;
                    }
                    catch (Exception)
                    {
                        // The bridge may not be running; keep the game unaffected.
                    }
                }
                Thread.Sleep(33);
            }
        }
    }

    internal static class LedState
    {
        private sealed class ButtonLed
        {
            public byte r, g, b;
            public byte toR, toG, toB;
            public long begin;
            public long duration;
        }

        private static readonly object Gate = new object();
        private static readonly ButtonLed[] Buttons = new ButtonLed[8];
        private static readonly Stopwatch Clock = Stopwatch.StartNew();
        private static byte body;
        private static byte circle;
        private static byte side;
        private static bool valid;
        private static bool dirty;

        static LedState()
        {
            for (int i = 0; i < Buttons.Length; i++) Buttons[i] = new ButtonLed();
        }

        internal static void Reset()
        {
            lock (Gate)
            {
                for (int i = 0; i < Buttons.Length; i++)
                {
                    ButtonLed led = Buttons[i];
                    led.r = led.g = led.b = 0;
                    led.duration = 0;
                }
                body = circle = side = 0;
                valid = false;
                dirty = false;
            }
        }

        internal static void SetButton(int index, Color32 color)
        {
            if (index < 0 || index >= Buttons.Length) return;
            lock (Gate)
            {
                ButtonLed led = Buttons[index];
                led.r = color.r;
                led.g = color.g;
                led.b = color.b;
                led.duration = 0;
                valid = true;
                dirty = true;
            }
        }

        internal static void SetAll(Color32 color)
        {
            lock (Gate)
            {
                for (int i = 0; i < Buttons.Length; i++)
                {
                    ButtonLed led = Buttons[i];
                    led.r = color.r;
                    led.g = color.g;
                    led.b = color.b;
                    led.duration = 0;
                }
                valid = true;
                dirty = true;
            }
        }

        internal static long Now
        {
            get { return Clock.ElapsedMilliseconds; }
        }

        internal static void FadeAll(Color32 color, byte speed)
        {
            long now = Now;
            long duration = speed == 0 ? 0 : (long)Math.Round(4095.0 / speed * 8.0);
            lock (Gate)
            {
                for (int i = 0; i < Buttons.Length; i++)
                {
                    ButtonLed led = Buttons[i];
                    led.toR = color.r;
                    led.toG = color.g;
                    led.toB = color.b;
                    led.duration = duration;
                    led.begin = now;
                }
                valid = true;
                dirty = true;
            }
        }

        internal static void SetBrightness(int index, byte intensity)
        {
            lock (Gate)
            {
                if (index == 8) body = intensity;
                else if (index == 9) circle = intensity;
                else if (index == 10) side = intensity;
                else return;
                valid = true;
                dirty = true;
            }
        }

        internal static void SetMultiBrightness(Color32 color)
        {
            lock (Gate)
            {
                body = color.r;
                circle = color.g;
                side = color.b;
                valid = true;
                dirty = true;
            }
        }

        internal static void AllOff()
        {
            lock (Gate)
            {
                for (int i = 0; i < Buttons.Length; i++)
                {
                    ButtonLed led = Buttons[i];
                    led.r = led.g = led.b = 0;
                    led.duration = 0;
                }
                body = circle = side = 0;
                valid = true;
                dirty = true;
            }
        }

        internal static void Tick(long now)
        {
            lock (Gate)
            {
                for (int i = 0; i < Buttons.Length; i++)
                {
                    ButtonLed led = Buttons[i];
                    if (led.duration <= 0) continue;
                    long elapsed = now - led.begin;
                    if (elapsed >= led.duration)
                    {
                        led.r = led.toR;
                        led.g = led.toG;
                        led.b = led.toB;
                        led.duration = 0;
                    }
                    else
                    {
                        float t = (float)elapsed / led.duration;
                        led.r = Lerp(led.r, led.toR, t);
                        led.g = Lerp(led.g, led.toG, t);
                        led.b = Lerp(led.b, led.toB, t);
                    }
                    dirty = true;
                }
            }
        }

        private static byte Lerp(byte from, byte to, float t)
        {
            return (byte)(from + (to - from) * t);
        }

        internal static bool NeedsSend(long now, long lastSend)
        {
            lock (Gate)
            {
                // 1 Hz keepalive lets a reconnected iPad recover the state.
                return valid && (dirty || now - lastSend >= 1000);
            }
        }

        internal static byte[] BuildPacket()
        {
            byte[] packet = new byte[33];
            packet[0] = (byte)'M';
            packet[1] = (byte)'P';
            packet[2] = (byte)'L';
            packet[3] = (byte)'1';
            packet[4] = 1;
            lock (Gate)
            {
                packet[5] = (byte)(valid ? 1 : 0);
                for (int i = 0; i < Buttons.Length; i++)
                {
                    ButtonLed led = Buttons[i];
                    packet[6 + i * 3] = led.r;
                    packet[7 + i * 3] = led.g;
                    packet[8 + i * 3] = led.b;
                }
                packet[30] = body;
                packet[31] = circle;
                packet[32] = side;
                dirty = false;
            }
            return packet;
        }
    }

    [HarmonyPatch(typeof(Bd15070_4IF), "_setColor")]
    internal static class SetColorPatch
    {
        private static void Prefix(byte ledPos, Color32 color, Bd15070_4IF.InitParam ____initParam)
        {
            if (____initParam == null || ____initParam.index != 0) return;
            LedState.SetButton(ledPos, color);
        }
    }

    [HarmonyPatch(typeof(Bd15070_4IF), "_setColorMulti")]
    internal static class SetColorMultiPatch
    {
        private static void Prefix(Color32 color, Bd15070_4IF.InitParam ____initParam)
        {
            if (____initParam == null || ____initParam.index != 0) return;
            LedState.SetAll(color);
        }
    }

    [HarmonyPatch(typeof(Bd15070_4IF), "_setColorMultiFade")]
    internal static class SetColorMultiFadePatch
    {
        private static void Prefix(Color32 color, byte speed, Bd15070_4IF.InitParam ____initParam)
        {
            if (____initParam == null || ____initParam.index != 0) return;
            LedState.FadeAll(color, speed);
        }
    }

    [HarmonyPatch(typeof(Bd15070_4IF), "_setColorFet")]
    internal static class SetColorFetPatch
    {
        private static void Prefix(byte ledPos, byte color, Bd15070_4IF.InitParam ____initParam)
        {
            if (____initParam == null || ____initParam.index != 0) return;
            LedState.SetBrightness(ledPos, color);
        }
    }

    [HarmonyPatch(typeof(Bd15070_4IF), "_setColorMultiFet")]
    internal static class SetColorMultiFetPatch
    {
        private static void Prefix(Color32 color, Bd15070_4IF.InitParam ____initParam)
        {
            if (____initParam == null || ____initParam.index != 0) return;
            LedState.SetMultiBrightness(color);
        }
    }

    [HarmonyPatch(typeof(Bd15070_4IF), "_setLedAllOff")]
    internal static class LedAllOffPatch
    {
        private static void Prefix(Bd15070_4IF.InitParam ____initParam)
        {
            if (____initParam == null || ____initParam.index != 0) return;
            LedState.AllOff();
        }
    }
}
