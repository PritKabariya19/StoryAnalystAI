// Firebase client SDK initialization
import { initializeApp } from "firebase/app";
import { getAuth, GoogleAuthProvider } from "firebase/auth";

const firebaseConfig = {
  apiKey:            import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain:        import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId:         import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket:     import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId:             import.meta.env.VITE_FIREBASE_APP_ID,
};

// Treat the .env.example placeholders ("your_...") as not configured
const isFirebaseConfigured = Boolean(firebaseConfig.apiKey) && !firebaseConfig.apiKey.startsWith("your_");

let auth = null;
let googleProvider = null;

if (isFirebaseConfigured) {
  auth = getAuth(initializeApp(firebaseConfig));
  googleProvider = new GoogleAuthProvider();
} else {
  console.warn("⚠️  Firebase not configured — only local email/password login is available.");
}

export { auth, googleProvider, isFirebaseConfigured };
