import { create } from "zustand";
import { persist } from "zustand/middleware";
import { authLogin, authSignup, authMe } from "../services/api";
import * as authService from "../services/authService";
import { auth, isFirebaseConfigured } from "../services/firebase";

// Decode JWT manually to get user data across reloads
function parseJwt(token) {
  try {
    const base64Url = token.split(".")[1];
    const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
    const jsonPayload = decodeURIComponent(
      atob(base64)
        .split("")
        .map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
        .join("")
    );
    return JSON.parse(jsonPayload);
  } catch (e) {
    return null;
  }
}

const fromLocal = (u) => ({ uid: u.uid, email: u.email, displayName: u.name });

/** Replace the free-plan defaults with the stored profile. On failure the user stays signed in. */
async function loadProfile(set, get) {
  const uid = get().user?.uid;
  try {
    const { user } = await authMe();
    if (uid && get().user?.uid === uid) {
      set({ plan: user.plan, usageCount: user.usageCount });
    }
  } catch (err) {
    console.warn("Could not load the user profile:", err.message);
  }
}

/** Start a Django (local JWT) session, ending any Firebase one. */
async function startLocalSession(set, get, { user, token }) {
  localStorage.setItem("token", token);
  if (isFirebaseConfigured && auth.currentUser) await authService.signOutFirebase();
  set({ user: fromLocal(user), plan: user.plan || "free", usageCount: 0 });
  loadProfile(set, get);
}

// ─── Zustand Store ────────────────────────────────────────────────────────────
const useStore = create(
  persist(
    (set, get) => ({
      // ── Auth State ────────────────────────────────────────────────────────────
      user: null,
      plan: "free",
      usageCount: 0,
      authLoading: true,      // restoring the session on page load
      authSubmitting: false,  // a sign-in or sign-up request is running
      authError: null,

      // ── UI State ──────────────────────────────────────────────────────────────
      theme: "dark",
      toasts: [],

      // ── Generation State ──────────────────────────────────────────────────────
      storyOutput: null,
      explorerOutput: null,
      smartTestOutput: null,
      testResults: null,
      generatedTests: [],
      executedResults: [],
      generationLoading: false,
      generationError: null,
      activeReports: [],

      // ── Input State ───────────────────────────────────────────────────────────
      globalUrl: "",
      globalQuery: "",
      globalUserStory: "",
      globalRequirement: "",
      globalDepth: 1,

      setGlobalUrl: (url) => set({ globalUrl: url }),
      setGlobalQuery: (query) => set({ globalQuery: query }),
      setGlobalUserStory: (story) => set({ globalUserStory: story }),
      setGlobalRequirement: (req) => set({ globalRequirement: req }),
      setGlobalDepth: (depth) => set({ globalDepth: depth }),
      
      setSmartTestOutput: (data) => set({ smartTestOutput: data }),

      // ── Auth Actions ──────────────────────────────────────────────────────────
      initAuth: () => {
        // 1. Restore a local JWT session
        const token = localStorage.getItem("token");
        const payload = token ? parseJwt(token) : null;
        if (payload && payload.exp * 1000 > Date.now()) {
          set({ user: fromLocal(payload), plan: payload.plan || "free", authLoading: false });
          loadProfile(set, get);
        } else if (token) {
          localStorage.removeItem("token");
        }

        // 2. Always watch Firebase, so sign-in still works after a local logout
        if (isFirebaseConfigured) {
          return authService.watchAuthState((firebaseUser) => {
            if (firebaseUser) {
              localStorage.removeItem("token");
              const isNewUser = get().user?.uid !== firebaseUser.uid;
              set({
                user: authService.toUser(firebaseUser),
                ...(isNewUser && { plan: "free", usageCount: 0 }),
                authLoading: false,
              });
              loadProfile(set, get);
            } else if (!localStorage.getItem("token")) {
              set({ user: null, plan: "free", usageCount: 0, authLoading: false });
            }
          });
        }

        set({ authLoading: false });
        return () => {};
      },

      signup: async (email, password, name) => {
        set({ authSubmitting: true, authError: null });
        try {
          if (isFirebaseConfigured) {
            const user = await authService.signUpWithEmail(email, password, name);
            localStorage.removeItem("token");
            set({ user });
            await loadProfile(set, get);
          } else {
            await startLocalSession(set, get, await authSignup({ email, password, name }));
          }
          set({ authSubmitting: false });
        } catch (err) {
          set({ authSubmitting: false, authError: authService.authErrorMessage(err) });
          throw err;
        }
      },

      login: async (email, password) => {
        set({ authSubmitting: true, authError: null });
        try {
          let useLocal = !isFirebaseConfigured;
          if (!useLocal) {
            try {
              await authService.signInWithEmail(email, password);
              localStorage.removeItem("token");
            } catch (err) {
              if (!authService.isBadCredential(err)) throw err;
              useLocal = true; // may be an account created before Firebase
            }
          }
          if (useLocal) {
            await startLocalSession(set, get, await authLogin({ email, password }));
          }
          set({ authSubmitting: false });
        } catch (err) {
          set({ authSubmitting: false, authError: authService.authErrorMessage(err) });
          throw err;
        }
      },

      loginWithGoogle: async () => {
        if (!isFirebaseConfigured) {
          set({ authError: "Google sign-in is not configured." });
          throw new Error("Not configured");
        }
        set({ authSubmitting: true, authError: null });
        try {
          await authService.signInWithGoogle();
          localStorage.removeItem("token");
          set({ authSubmitting: false });
        } catch (err) {
          set({
            authSubmitting: false,
            authError: authService.isPopupDismissed(err) ? null : authService.authErrorMessage(err),
          });
          throw err;
        }
      },

      resetPassword: async (email) => {
        if (!isFirebaseConfigured) {
          set({ authError: "Password reset is not configured." });
          throw new Error("Not configured");
        }
        set({ authError: null });
        try {
          await authService.resetPassword(email);
        } catch (err) {
          if (err.code === "auth/user-not-found") return; // don't reveal which emails exist
          set({ authError: authService.authErrorMessage(err) });
          throw err;
        }
      },

      clearAuthError: () => set({ authError: null }),

      logout: async () => {
        localStorage.removeItem("token");
        if (isFirebaseConfigured && auth.currentUser) {
          await authService.signOutFirebase();
        }
        set({
          user: null, plan: "free", usageCount: 0,
          storyOutput: null, explorerOutput: null, combinedOutput: null, testResults: null
        });
      },

      setPlan: (plan) => set({ plan }),
      setUsageCount: (count) => set({ usageCount: count }),

      // ── UI Actions ────────────────────────────────────────────────────────────
      toggleTheme: () => set((s) => ({ theme: s.theme === "dark" ? "light" : "dark" })),

      addToast: (message, type = "info") => {
        const id = Date.now();
        set((s) => ({ toasts: [...s.toasts, { id, message, type }] }));
        setTimeout(() => {
          set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
        }, 4000);
      },

      removeToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

      // ── Generation Actions ────────────────────────────────────────────────────
      setStoryOutput: (data) => set({ storyOutput: data }),
      setExplorerOutput: (data) => set({ explorerOutput: data }),
      setCombinedOutput: (data) => set({ combinedOutput: data }),
      setTestResults: (data) => set({ testResults: data }),
      setGeneratedTests: (tests) => set({ generatedTests: tests }),
      setExecutedResults: (results) => set({ executedResults: results }),
      setGenerationLoading: (v) => set({ generationLoading: v }),
      setGenerationError: (e) => set({ generationError: e }),
      clearGenerationState: () => set({ 
        storyOutput: null, explorerOutput: null, combinedOutput: null, 
        testResults: null, generatedTests: [], executedResults: [], generationError: null 
      }),

      addReport: (report) => set((s) => ({ activeReports: [report, ...s.activeReports].slice(0, 50) })),
      setReports: (reports) => set({ activeReports: reports }),
    }),
    {
      name: "storyanalyst-store",
      partialize: (s) => ({ theme: s.theme }),
    }
  )
);

export default useStore;
