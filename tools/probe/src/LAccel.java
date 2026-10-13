import java.lang.reflect.*;
import java.io.*;
import java.util.*;

public class LAccel {
    static PrintWriter out;
    public static void main(String[] args) throws Exception {
        int seconds = args.length > 0 ? Integer.parseInt(args[0]) : 20;
        String path = args.length > 1 ? args[1] : "/data/local/tmp/accel.txt";
        out = new PrintWriter(new BufferedWriter(new FileWriter(path, false)), true);
        Class.forName("android.os.Looper").getMethod("prepare").invoke(null);
        Object thread = Class.forName("android.app.ActivityThread").getMethod("systemMain").invoke(null);
        Object ctx = Class.forName("android.app.ActivityThread").getMethod("getSystemContext").invoke(thread);
        Class<?> contextCls = Class.forName("android.content.Context");
        Object sm = contextCls.getMethod("getSystemService", String.class).invoke(ctx, "sensor");
        Class<?> smCls = Class.forName("android.hardware.SensorManager");
        Object accel = smCls.getMethod("getDefaultSensor", int.class).invoke(sm, 1);
        out.println("#sensor " + accel);
        Class<?> listenerCls = Class.forName("android.hardware.SensorEventListener");
        Object listener = Proxy.newProxyInstance(LAccel.class.getClassLoader(), new Class<?>[]{listenerCls},
            new InvocationHandler() {
                public Object invoke(Object proxy, Method m, Object[] a) throws Throwable {
                    String n = m.getName();
                    if (n.equals("hashCode")) return 1;
                    if (n.equals("equals")) return proxy == a[0];
                    if (n.equals("toString")) return "LAccelListener";
                    if (n.equals("onSensorChanged")) {
                        Object ev = a[0];
                        float[] v = (float[]) ev.getClass().getField("values").get(ev);
                        long t = (Long) ev.getClass().getField("timestamp").get(ev);
                        out.println(String.format(Locale.US, "%d %.4f %.4f %.4f", t, v[0], v[1], v[2]));
                    }
                    return null;
                }
            });
        smCls.getMethod("registerListener", listenerCls,
            Class.forName("android.hardware.Sensor"), int.class).invoke(sm, listener, accel, 1);
        final long end = System.currentTimeMillis() + seconds * 1000L;
        new Thread(new Runnable() {
            public void run() {
                try { Thread.sleep(Math.max(1, end - System.currentTimeMillis())); } catch (Exception e) {}
                out.println("#done");
                out.close();
                Runtime.getRuntime().halt(0);
            }
        }).start();
        Class.forName("android.os.Looper").getMethod("loop").invoke(null);
    }
}