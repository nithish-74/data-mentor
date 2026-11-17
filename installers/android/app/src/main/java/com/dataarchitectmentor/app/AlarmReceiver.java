package com.dataarchitectmentor.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** An alarm went off (ring it, then set the next one), or the phone restarted / changed time (set alarms again). */
public class AlarmReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        final Context c = context.getApplicationContext();
        if (!Alarms.ACTION_RING.equals(intent.getAction())) {
            Alarms.scheduleNext(c);
            return;
        }
        final String slot = intent.getStringExtra(Alarms.EXTRA_SLOT);
        final PendingResult done = goAsync();   // the network check runs off the main thread
        new Thread(() -> {
            try {
                Alarms.ring(c, slot == null ? "" : slot);
            } finally {
                if (!"test".equals(slot)) Alarms.scheduleNext(c);
                done.finish();
            }
        }).start();
    }
}
