// Portal 112 — sincronización compartida (Firebase Realtime Database + Auth).
//
// Un único archivo para que Turnos y Mi Panel no dupliquen la configuración
// ni la lógica de login. Se carga como <script type="module">, así que los
// scripts "clásicos" de cada módulo (que no pueden usar `import`) hablan con
// él a través de `window.PortalSync` y del callback `window.onPortalAuthReady`.
//
// Importante: estos valores de firebaseConfig NO son secretos — están pensados
// para ir en código público. La seguridad real la ponen las Reglas de la base
// de datos (Realtime Database → Rules), que exigen estar autenticado para
// leer/escribir en cada ruta.
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.13.2/firebase-app.js";
import {
  getAuth, onAuthStateChanged, signInWithEmailAndPassword, signOut,
  setPersistence, browserLocalPersistence
} from "https://www.gstatic.com/firebasejs/10.13.2/firebase-auth.js";
import {
  getDatabase, ref, get, set, onValue
} from "https://www.gstatic.com/firebasejs/10.13.2/firebase-database.js";

const firebaseConfig = {
  apiKey: "AIzaSyCzdrnL6qWbG9OHPiQV43mJvgH9i56PkVQ",
  authDomain: "portal-112-b1754.firebaseapp.com",
  databaseURL: "https://portal-112-b1754-default-rtdb.europe-west1.firebasedatabase.app",
  projectId: "portal-112-b1754",
  storageBucket: "portal-112-b1754.firebasestorage.app",
  messagingSenderId: "84139749968",
  appId: "1:84139749968:web:03fd5a508e32a5ebf25755"
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getDatabase(app);

let currentUser = null;
const authListeners = [];

function notifyAuth(user) {
  authListeners.forEach(function (fn) { try { fn(user); } catch (e) { /* un listener roto no debe tirar abajo los demás */ } });
  // Puente hacia el script clásico del módulo (no puede usar `import`).
  if (window.onPortalAuthReady) { try { window.onPortalAuthReady(user); } catch (e) { /* idem */ } }
}

// Si esto falla (p. ej. almacenamiento del navegador restringido), la sesión
// simplemente no se recuerda entre visitas — la app sigue funcionando igual.
setPersistence(auth, browserLocalPersistence).catch(function () {});

onAuthStateChanged(auth, function (user) {
  currentUser = user;
  notifyAuth(user);
});

window.PortalSync = {
  // Se dispara cada vez que cambia el estado de sesión (login, logout, o al
  // resolverse la sesión guardada al cargar la página).
  onAuthChange: function (fn) { authListeners.push(fn); },
  isLoggedIn: function () { return !!currentUser; },
  currentUser: function () { return currentUser; },
  login: function (email, password) { return signInWithEmailAndPassword(auth, email, password); },
  logout: function () { return signOut(auth); },
  // path tipo "turnos/state" o "panelControl/config".
  saveData: function (path, data) { return set(ref(db, path), data); },
  loadData: function (path) {
    return get(ref(db, path)).then(function (snap) { return snap.exists() ? snap.val() : null; });
  },
  watchData: function (path, callback) {
    return onValue(ref(db, path), function (snap) { callback(snap.exists() ? snap.val() : null); });
  }
};
