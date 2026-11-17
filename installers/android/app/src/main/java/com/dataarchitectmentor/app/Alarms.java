package com.dataarchitectmentor.app;

import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.media.AudioAttributes;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.Build;
import android.webkit.CookieManager;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.List;
import java.util.TimeZone;

/**
 * Study reminders that ring like alarms. The learner picks the times on the You page; the site hands them to
 * the app (MainActivity.Bridge.setSchedule). Each time is a real Android alarm, so it rings even when the app
 * is closed or the phone is offline. When it goes off we ask the site what to say (today's lesson, a nudge...)
 * and fall back to a plain "Time to learn" if there's no connection.
 */
final class Alarms {
    static final String EXTRA_PATH = "path";
    static final String EXTRA_SLOT = "slot";
    static final String ACTION_RING = "com.dataarchitectmentor.app.RING";
    static final String USER_AGENT = "DataMentorApp/" + BuildConfig.VERSION_NAME + " (Android " + Build.VERSION.RELEASE + ")";
    private static final String PREFS = "mentor";

    private Alarms() { }

    // ---- schedule ------------------------------------------------------------------------------

    static void saveSchedule(Context c, String json) {
        try {
            new JSONObject(json);  // only keep valid data
            c.getSharedPreferences(PREFS, 0).edit().putString("schedule", json).apply();
        } catch (Exception ignored) { }
    }

    private static JSONObject schedule(Context c) {
        try {
            String s = c.getSharedPreferences(PREFS, 0).getString("schedule", null);
            if (s != null) return new JSONObject(s);
        } catch (Exception ignored) { }
        try {  // before the learner first opens the app: morning and evening
            return new JSONObject("{\"times\":[\"07:00\",\"20:00\"],\"sound\":true,\"vibrate\":true,\"enabled\":true,"
                + "\"timezone\":\"Asia/Kolkata\"}");
        } catch (Exception e) {
            return new JSONObject();
        }
    }

    /** Sets one Android alarm for the next of the learner's times (the receiver sets the following one). */
    static void scheduleNext(Context c) {
        JSONObject s = schedule(c);
        AlarmManager am = c.getSystemService(AlarmManager.class);
        PendingIntent pi = ringIntent(c, 1, null);
        am.cancel(pi);
        if (!s.optBoolean("enabled", true)) return;
        JSONArray times = s.optJSONArray("times");
        if (times == null || times.length() == 0) return;

        TimeZone tz = TimeZone.getTimeZone(s.optString("timezone", TimeZone.getDefault().getID()));
        long now = System.currentTimeMillis(), best = Long.MAX_VALUE;
        String bestSlot = null;
        for (int i = 0; i < times.length(); i++) {
            String t = times.optString(i);
            String[] hm = t.split(":");
            if (hm.length != 2) continue;
            Calendar cal = Calendar.getInstance(tz);
            cal.set(Calendar.HOUR_OF_DAY, Integer.parseInt(hm[0]));
            cal.set(Calendar.MINUTE, Integer.parseInt(hm[1]));
            cal.set(Calendar.SECOND, 0);
            cal.set(Calendar.MILLISECOND, 0);
            if (cal.getTimeInMillis() <= now + 1000) cal.add(Calendar.DAY_OF_YEAR, 1);
            if (cal.getTimeInMillis() < best) { best = cal.getTimeInMillis(); bestSlot = t; }
        }
        if (bestSlot == null) return;
        setAlarm(c, best, ringIntent(c, 1, bestSlot));
    }

    static void scheduleTest(Context c) {
        setAlarm(c, System.currentTimeMillis() + 4000, ringIntent(c, 2, "test"));
    }

    private static void setAlarm(Context c, long at, PendingIntent pi) {
        AlarmManager am = c.getSystemService(AlarmManager.class);
        boolean exact = Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms();
        if (exact) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi);
        else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi);
    }

    private static PendingIntent ringIntent(Context c, int code, String slot) {
        Intent i = new Intent(c, AlarmReceiver.class).setAction(ACTION_RING);
        if (slot != null) i.putExtra(EXTRA_SLOT, slot);
        return PendingIntent.getBroadcast(c, code, i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    // ---- ringing --------------------------------------------------------------------------------

    /** Runs on a background thread when an alarm goes off. */
    static void ring(Context c, String slot) {
        boolean test = "test".equals(slot);
        List<String[]> msgs = new ArrayList<>();
        try {
            String query = "/api/app/digest?" + (test ? "test=1" : "slot=" + Uri.encode(slot));
            JSONObject r = null;
            Exception last = null;
            for (String base : new String[]{MainActivity.PRIMARY, MainActivity.BACKUP}) {  // main address, then backup
                if (base.isEmpty()) continue;
                try { r = new JSONObject(get(base, query)); break; } catch (Exception e) { last = e; }
            }
            if (r == null) throw last != null ? last : new Exception("no address");
            JSONArray items = r.optJSONArray("notifications");
            for (int i = 0; items != null && i < items.length(); i++) {
                JSONObject m = items.getJSONObject(i);
                msgs.add(new String[]{m.optString("title"), m.optString("body"), m.optString("url", "/#home"),
                                      m.optString("tag", "study")});
            }
            JSONObject sched = r.optJSONObject("schedule");
            if (sched != null) saveSchedule(c, sched.toString());  // stay in sync with changes made elsewhere
        } catch (Exception offlineOrSignedOut) {
            msgs.add(new String[]{"Time to learn", "Open Mentor for today's lesson.", "/#home", "study"});
        }
        JSONObject s = schedule(c);
        boolean sound = s.optBoolean("sound", true), vibrate = s.optBoolean("vibrate", true);
        for (int i = 0; i < msgs.size(); i++) {
            String[] m = msgs.get(i);
            boolean first = i == 0;  // only the first one rings; extras (e.g. new articles) arrive quietly with it
            notify(c, 100 + i, m[0], m[1], m[2], first && sound, first && vibrate);
        }
    }

    /** First address that answers, main one first; null if neither does. Runs off the main thread. */
    static String firstWorkingSite() {
        for (String base : new String[]{MainActivity.PRIMARY, MainActivity.BACKUP}) {
            if (base.isEmpty()) continue;
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(base + "/api/config").openConnection();
                c.setConnectTimeout(6000);
                c.setReadTimeout(6000);
                c.setRequestProperty("User-Agent", USER_AGENT);
                boolean ok = c.getResponseCode() == 200 && c.getHeaderField("ngrok-error-code") == null;
                c.disconnect();
                if (ok) return base;
            } catch (Exception ignored) { }
        }
        return null;
    }

    private static String get(String base, String path) throws Exception {
        HttpURLConnection con = (HttpURLConnection) new URL(base + path).openConnection();
        con.setConnectTimeout(8000);
        con.setReadTimeout(8000);
        con.setRequestProperty("User-Agent", USER_AGENT);
        con.setRequestProperty("ngrok-skip-browser-warning", "1");
        String cookie = CookieManager.getInstance().getCookie(base);
        if (cookie != null) con.setRequestProperty("Cookie", cookie);
        if (con.getResponseCode() != 200) throw new Exception("HTTP " + con.getResponseCode());
        StringBuilder sb = new StringBuilder();
        try (BufferedReader br = new BufferedReader(new InputStreamReader(con.getInputStream(), StandardCharsets.UTF_8))) {
            for (String line; (line = br.readLine()) != null; ) sb.append(line);
        }
        return sb.toString();
    }

    // ---- notifications ------------------------------------------------------------------------------

    /** Android fixes a channel's sound/vibration once created, so there is one channel per combination. */
    private static String channel(Context c, boolean sound, boolean vibrate) {
        String id = "study_" + (sound ? "s" : "") + (vibrate ? "v" : "") + "_v1";
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(id) == null) {
            String name = sound && vibrate ? "Study alarms (sound + vibration)" : sound ? "Study alarms (sound)"
                        : vibrate ? "Study alarms (vibration)" : "Study reminders (silent)";
            NotificationChannel ch = new NotificationChannel(id, name,
                sound || vibrate ? NotificationManager.IMPORTANCE_HIGH : NotificationManager.IMPORTANCE_DEFAULT);
            ch.setDescription("Your study reminders, at the times you picked in Mentor");
            if (sound) {
                Uri alarm = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
                if (alarm == null) alarm = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION);
                ch.setSound(alarm, new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build());
            } else {
                ch.setSound(null, null);
            }
            ch.enableVibration(vibrate);
            if (vibrate) ch.setVibrationPattern(new long[]{0, 700, 350, 700, 350, 700});
            ch.enableLights(true);
            ch.setLightColor(0xFF7FB2DD);
            nm.createNotificationChannel(ch);
        }
        return id;
    }

    private static void notify(Context c, int id, String title, String body, String path, boolean sound, boolean vibrate) {
        Intent open = new Intent(c, MainActivity.class).putExtra(EXTRA_PATH, path)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent tap = PendingIntent.getActivity(c, id, open,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
            ? new Notification.Builder(c, channel(c, sound, vibrate))
            : new Notification.Builder(c);
        b.setSmallIcon(R.drawable.ic_notification)
         .setColor(0xFF16202B)
         .setContentTitle(title)
         .setContentText(body)
         .setStyle(new Notification.BigTextStyle().bigText(body))
         .setCategory(Notification.CATEGORY_REMINDER)
         .setContentIntent(tap)
         .setAutoCancel(true);
        if (Build.VERSION.SDK_INT < 26) {
            int defaults = (sound ? Notification.DEFAULT_SOUND : 0) | (vibrate ? Notification.DEFAULT_VIBRATE : 0);
            b.setDefaults(defaults).setPriority(sound || vibrate ? Notification.PRIORITY_HIGH : Notification.PRIORITY_DEFAULT);
        }
        c.getSystemService(NotificationManager.class).notify(id, b.build());
    }
}
