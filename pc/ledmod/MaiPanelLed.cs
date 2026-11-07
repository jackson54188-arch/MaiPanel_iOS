// Reads both players' RGB LED state out of maimai DX and streams it to the
// bridge. The game computes LED colours inside Mecha.Bd15070_4IF; these are
// the same private methods AquaMai hooks for Maimoller hardware, so the
// signatures are proven against SDEZ 1.60.
//
// Each datagram is 33 bytes: "MPL1" | player (1 = 1P, 2 = 2P) |
// valid:u8 | 8 x RGB | body | circle | side.

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

[assembly: MelonInfo(typeof(MaiPanelLed.MaiPanelLedMod), "MaiPanelLed", "0.2.0", "MaiPanel")]
[assembly: MelonGame(null, null)]

namespace MaiPanelLed
{
    public sealed class MaiPanelLedMod : MelonMod
    {
        private const int Port = 24872;
        private const int PlayerCount = 2;
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
            LoggerInstance.Msg("LED 已启动 (1P/2P) -> 127.0.0.1:" + Port);
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
            long[] lastSend = new long[PlayerCount];
            while (running)
            {
                long now = LedState.Now;
                LedState.Tick(now);
                for (int player = 0; player < PlayerCount; player++)
                {
                    if (!LedState.NeedsSend(player, now, lastSend[player])) continue;
                    byte[] packet = LedState.BuildPacket(player);
                    try
                    {
                        udp.Send(packet, packet.Length, target);
                        lastSend[player] = now;
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
        private const int PlayerCount = 2;

        private sealed class ButtonLed
        {
            public byte r, g, b;
            public byte toR, toG, toB;
            public long begin;
            public long duration;
        }

        private sealed class PlayerLed
        {
            public readonly ButtonLed[] Buttons = new ButtonLed[8];
            public byte body;
            public byte circle;
            public byte side;
            public bool valid;
            public bool dirty;

            public PlayerLed()
            {
                for (int i = 0; i < Buttons.Length; i++) Buttons[i] = new ButtonLed();
            }
        }

        private static readonly object Gate = new object();
        private static readonly PlayerLed[] Players = new PlayerLed[PlayerCount];
        private static readonly Stopwatch Clock = Stopwatch.StartNew();

        static LedState()
        {
            for (int i = 0; i < Players.Length; i++) Players[i] = new PlayerLed();
        }

        private static PlayerLed Of(int player)
        {
            return player >= 0 && player < Players.Length ? Players[player] : null;
        }

        internal static void Reset()
        {
            lock (Gate)
            {
                for (int p = 0; p < Players.Length; p++)
                {
                    PlayerLed state = Players[p];
                    for (int i = 0; i < state.Buttons.Length; i++)
                    {
                        ButtonLed led = state.Buttons[i];
                        led.r = led.g = led.b = 0;
                        led.duration = 0;
                    }
                    state.body = state.circle = state.side = 0;
                    state.valid = false;
                    state.dirty = false;
                }
            }
        }

        internal static void SetButton(int player, int index, Color32 color)
        {
            PlayerLed state = Of(player);
            if (state == null || index < 0 || index >= state.Buttons.Length) return;
            lock (Gate)
            {
                ButtonLed led = state.Buttons[index];
                led.r = color.r;
                led.g = color.g;
                led.b = color.b;
                led.duration = 0;
                state.valid = true;
                state.dirty = true;
            }
        }

        internal static void SetAll(int player, Color32 color)
        {
            PlayerLed state = Of(player);
            if (state == null) return;
            lock (Gate)
            {
                for (int i = 0; i < state.Buttons.Length; i++)
                {
                    ButtonLed led = state.Buttons[i];
                    led.r = color.r;
                    led.g = color.g;
                    led.b = color.b;
                    led.duration = 0;
                }
                state.valid = true;
                state.dirty = true;
            }
        }

        internal static long Now
        {
            get { return Clock.ElapsedMilliseconds; }
        }

        internal static void FadeAll(int player, Color32 color, byte speed)
        {
            PlayerLed state = Of(player);
            if (state == null) return;
            long now = Now;
            long duration = speed == 0 ? 0 : (long)Math.Round(4095.0 / speed * 8.0);
            lock (Gate)
            {
                for (int i = 0; i < state.Buttons.Length; i++)
                {
                    ButtonLed led = state.Buttons[i];
                    led.toR = color.r;
                    led.toG = color.g;
                    led.toB = color.b;
                    led.duration = duration;
                    led.begin = now;
                }
                state.valid = true;
                state.dirty = true;
            }
        }

        internal static void SetBrightness(int player, int index, byte intensity)
        {
            PlayerLed state = Of(player);
            if (state == null) return;
            lock (Gate)
            {
                if (index == 8) state.body = intensity;
                else if (index == 9) state.circle = intensity;
                else if (index == 10) state.side = intensity;
                else return;
                state.valid = true;
                state.dirty = true;
            }
        }

        internal static void SetMultiBrightness(int player, Color32 color)
        {
            PlayerLed state = Of(player);
            if (state == null) return;
            lock (Gate)
            {
                state.body = color.r;
                state.circle = color.g;
                state.side = color.b;
                state.valid = true;
                state.dirty = true;
            }
        }

        internal static void AllOff(int player)
        {
            PlayerLed state = Of(player);
            if (state == null) return;
            lock (Gate)
            {
                for (int i = 0; i < state.Buttons.Length; i++)
                {
                    ButtonLed led = state.Buttons[i];
                    led.r = led.g = led.b = 0;
                    led.duration = 0;
                }
                state.body = state.circle = state.side = 0;
                state.valid = true;
                state.dirty = true;
            }
        }

        internal static void Tick(long now)
        {
            lock (Gate)
            {
                for (int p = 0; p < Players.Length; p++)
                {
                    PlayerLed state = Players[p];
                    for (int i = 0; i < state.Buttons.Length; i++)
                    {
                        ButtonLed led = state.Buttons[i];
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
                        state.dirty = true;
                    }
                }
            }
        }

        private static byte Lerp(byte from, byte to, float t)
        {
            return (byte)(from + (to - from) * t);
        }

        internal static bool NeedsSend(int player, long now, long lastSend)
        {
            PlayerLed state = Of(player);
            if (state == null) return false;
            lock (Gate)
            {
                // 1 Hz keepalive lets a reconnected iPad recover the state.
                return state.valid && (state.dirty || now - lastSend >= 1000);
            }
        }

        internal static byte[] BuildPacket(int player)
        {
            PlayerLed state = Of(player);
            byte[] packet = new byte[33];
            packet[0] = (byte)'M';
            packet[1] = (byte)'P';
            packet[2] = (byte)'L';
            packet[3] = (byte)'1';
            packet[4] = (byte)(player + 1);
            if (state == null) return packet;
            lock (Gate)
            {
                packet[5] = (byte)(state.valid ? 1 : 0);
                for (int i = 0; i < state.Buttons.Length; i++)
                {
                    ButtonLed led = state.Buttons[i];
                    packet[6 + i * 3] = led.r;
                    packet[7 + i * 3] = led.g;
                    packet[8 + i * 3] = led.b;
                }
                packet[30] = state.body;
                packet[31] = state.circle;
                packet[32] = state.side;
                state.dirty = false;
            }
            return packet;
        }
    }

    [HarmonyPatch(typeof(Bd15070_4IF), "_setColor")]
    internal static class SetColorPatch
    {
        private static void Prefix(byte ledPos, Color32 color, Bd15070_4IF.InitParam ____initParam)
        {
            if (____initParam == null) return;
            LedState.SetButton(____initParam.index, ledPos, color);
        }
    }

    [HarmonyPatch(typeof(Bd15070_4IF), "_setColorMulti")]
    internal static class SetColorMultiPatch
    {
        private static void Prefix(Color32 color, Bd15070_4IF.InitParam ____initParam)
        {
            if (____initParam == null) return;
            LedState.SetAll(____initParam.index, color);
        }
    }

    [HarmonyPatch(typeof(Bd15070_4IF), "_setColorMultiFade")]
    internal static class SetColorMultiFadePatch
    {
        private static void Prefix(Color32 color, byte speed, Bd15070_4IF.InitParam ____initParam)
        {
            if (____initParam == null) return;
            LedState.FadeAll(____initParam.index, color, speed);
        }
    }

    [HarmonyPatch(typeof(Bd15070_4IF), "_setColorFet")]
    internal static class SetColorFetPatch
    {
        private static void Prefix(byte ledPos, byte color, Bd15070_4IF.InitParam ____initParam)
        {
            if (____initParam == null) return;
            LedState.SetBrightness(____initParam.index, ledPos, color);
        }
    }

    [HarmonyPatch(typeof(Bd15070_4IF), "_setColorMultiFet")]
    internal static class SetColorMultiFetPatch
    {
        private static void Prefix(Color32 color, Bd15070_4IF.InitParam ____initParam)
        {
            if (____initParam == null) return;
            LedState.SetMultiBrightness(____initParam.index, color);
        }
    }

    [HarmonyPatch(typeof(Bd15070_4IF), "_setLedAllOff")]
    internal static class LedAllOffPatch
    {
        private static void Prefix(Bd15070_4IF.InitParam ____initParam)
        {
            if (____initParam == null) return;
            LedState.AllOff(____initParam.index);
        }
    }
}
