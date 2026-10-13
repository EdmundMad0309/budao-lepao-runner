import java.lang.reflect.*;

public class LProbe {
    static boolean argsDump = false;
    static String OPS = "com.android.shell";
    static String prov = "network";

    static Object getSysService(String name) {
        try {
            Class<?> sm = Class.forName("android.os.ServiceManager");
            Object binder = sm.getMethod("getService", String.class).invoke(null, name);
            System.out.println("[probe] binder(" + name + ")=" + binder);
            return binder;
        } catch (Throwable t) { System.out.println("[probe] getService fail " + t); return null; }
    }

    static Object asInterface(String iface, Object binder) {
        try {
            Class<?> stub = Class.forName(iface + "$Stub");
            Class<?> ib = Class.forName("android.os.IBinder");
            return stub.getMethod("asInterface", ib).invoke(null, binder);
        } catch (Throwable t) { System.out.println("[probe] asInterface fail " + t); return null; }
    }

    static Object newLocationRequest() {
        try {
            Class<?> lr = Class.forName("android.location.LocationRequest");
            return lr.getMethod("create").invoke(null);
        } catch (Throwable t) { System.out.println("[probe] LR fail " + t); return null; }
    }

    static void dumpLoc(String tag, Object loc) {
        if (loc == null) { System.out.println("[probe] " + tag + " = null"); return; }
        if (loc instanceof java.util.List) {
            java.util.List<?> l = (java.util.List<?>) loc;
            System.out.println("[probe] " + tag + " LIST size=" + l.size());
            for (Object o : l) dumpLoc(tag + "[item]", o);
            return;
        }
        try {
            Class<?> lc = Class.forName("android.location.Location");
            Object lat = lc.getMethod("getLatitude").invoke(loc);
            Object lon = lc.getMethod("getLongitude").invoke(loc);
            Object acc = lc.getMethod("getAccuracy").invoke(loc);
            Object spd = lc.getMethod("getSpeed").invoke(loc);
            Object brg = lc.getMethod("getBearing").invoke(loc);
            Object prv = lc.getMethod("getProvider").invoke(loc);
            Object t = lc.getMethod("getTime").invoke(loc);
            Object mock;
            try { mock = lc.getMethod("isMock").invoke(loc); }
            catch (Throwable x) { mock = "n/a"; }
            System.out.println("[probe] " + tag + " provider=" + prv + " lat=" + lat + " lon=" + lon
                + " acc=" + acc + " speed=" + spd + " bearing=" + brg + " time=" + t + " isMock=" + mock);
        } catch (Throwable t) { System.out.println("[probe] dumpLoc fail " + t); }
    }

    static void viaActivityThread() {
        try {
            Class.forName("android.os.Looper").getMethod("prepare").invoke(null);
            Class<?> at = Class.forName("android.app.ActivityThread");
            Object thread = at.getMethod("systemMain").invoke(null);
            System.out.println("[probe] systemMain ok " + thread);
            Object ctx = null;
            try { ctx = at.getMethod("getSystemContext").invoke(thread); } catch (Throwable x) { System.out.println("[probe] getSystemContext fail " + x); }
            if (ctx == null) { try { ctx = Class.forName("android.app.AppGlobals").getMethod("getInitialApplication").invoke(null); } catch (Throwable x) { System.out.println("[probe] getInitialApplication fail " + x); } }
            if (ctx == null) { try { ctx = at.getMethod("currentApplication").invoke(null); } catch (Throwable x) { System.out.println("[probe] currentApplication fail " + x); } }
            System.out.println("[probe] systemContext=" + ctx);
            if (ctx == null) return;
            Class<?> contextCls = Class.forName("android.content.Context");
            try {
                Object pkgCtx = contextCls.getMethod("createPackageContext", String.class, int.class)
                    .invoke(ctx, OPS, 0);
                System.out.println("[probe] pkgContext=" + pkgCtx);
                if (pkgCtx != null) ctx = pkgCtx;
            } catch (Throwable x) {
                System.out.println("[probe] createPackageContext fail " + x);
                try {
                    Class<?> c = ctx.getClass();
                    while (c != null) {
                        try { java.lang.reflect.Field f = c.getDeclaredField("mOpPackageName"); f.setAccessible(true); f.set(ctx, OPS); System.out.println("[probe] mOpPackageName patched on " + c); break; }
                        catch (NoSuchFieldException nf) { c = c.getSuperclass(); }
                    }
                } catch (Throwable y) { System.out.println("[probe] op pkg patch fail " + y); }
            }
            Object lm = contextCls.getMethod("getSystemService", String.class).invoke(ctx, "location");
            System.out.println("[probe] LocationManager=" + lm);
            if (lm == null) return;
            Class<?> lmc = Class.forName("android.location.LocationManager");
            Class<?> lc = Class.forName("android.location.Location");
            for (String p : new String[]{"gps", "network", "fused", "passive"}) {
                try {
                    Object loc = lmc.getMethod("getLastKnownLocation", String.class).invoke(lm, p);
                    dumpLoc("lastKnown(" + p + ")", loc);
                } catch (Throwable t) { System.out.println("[probe] lastKnown(" + p + ") fail " + t + " cause=" + t.getCause()); }
            }
            Class<?> ll = Class.forName("android.location.LocationListener");
            Object listener = Proxy.newProxyInstance(LProbe.class.getClassLoader(), new Class<?>[]{ll},
                new InvocationHandler() {
                    public Object invoke(Object proxy, Method m, Object[] args) {
                        String n = m.getName();
                        if (n.equals("hashCode")) return 1;
                        if (n.equals("equals")) return proxy == args[0];
                        if (n.equals("toString")) return "LProbeListener";
                        if (n.equals("onLocationChanged")) dumpLoc("CALLBACK", args[0]);
                        else System.out.println("[probe] cb " + n);
                        return null;
                    }
                });
            for (String p : new String[]{"network", "gps"}) {
                try {
                    lmc.getMethod("requestLocationUpdates", String.class, long.class, float.class, ll)
                       .invoke(lm, p, 1000L, 0f, listener);
                    System.out.println("[probe] requested updates on " + p);
                } catch (Throwable t) { System.out.println("[probe] request(" + p + ") fail " + t + " cause=" + t.getCause()); }
            }
            Class<?> looper = Class.forName("android.os.Looper");
            System.out.println("[probe] entering looper");
            looper.getMethod("loop").invoke(null);
        } catch (Throwable t) { System.out.println("[probe] activityThread path fail " + t); t.printStackTrace(); }
    }
    
    static void viaServiceManager() {
        try {
            Object binder = getSysService("location");
            if (binder == null) return;
            Object lm = asInterface("android.location.ILocationManager", binder);
            System.out.println("[probe] ILocationManager=" + lm);
            if (lm == null) return;
            if (argsDump) { for (Method m : lm.getClass().getMethods()) { System.out.println("[probe] M " + m); } return; }
            Class<?> llrc = Class.forName("android.location.LastLocationRequest");
            Constructor<?> ctor = null;
            try { ctor = llrc.getDeclaredConstructor(); } catch (Throwable x) {}
            if (ctor == null) { try { ctor = llrc.getDeclaredConstructor(long.class, int.class, boolean.class); } catch (Throwable x) {} }
            System.out.println("[probe] LastLocationRequest ctor=" + ctor);
            if (ctor != null) ctor.setAccessible(true);
            Object llr = ctor == null ? null : ctor.newInstance(ctor.getParameterCount() == 3 ? new Object[]{0L, 0, false} : new Object[]{});
            for (String p : new String[]{"network", "gps", "fused"}) {
                try {
                    Object loc = lm.getClass().getMethod("getLastLocation", String.class, llrc, String.class, String.class)
                        .invoke(lm, p, llr, "com.lptiyu.tanke", null);
                    dumpLoc("svc.last(" + p + ")", loc);
                } catch (Throwable tt) { System.out.println("[probe] svc.last(" + p + ") fail " + tt); }
            }
        } catch (Throwable t) { System.out.println("[probe] svc path fail " + t); }
    }

    public static void main(String[] args) {
        System.out.println("[probe] start");
        for (String a : args) if (a.equals("dump")) argsDump = true;
        viaServiceManager();
        if (args.length > 0 && args[0].equals("svconly")) return;
        viaActivityThread();
    }
}