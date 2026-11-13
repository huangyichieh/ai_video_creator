# Story Studio frontend

Native Flutter Web client. See the [root README](../README.md) for setup, storage and backend startup.

Normally run `scripts/start-backend.ps1` from the repository root. It hosts the site and automatically rebuilds this frontend when its source changes. Refresh the browser once a build succeeds. The commands below are for the separate Flutter development server.

```powershell
flutter pub get
dart run build_runner build
flutter run -d web-server --web-port 7357 --dart-define=API_BASE_URL=http://127.0.0.1:8000
```

Feature folders contain only the controller/model/service/view layers they use. GetX owns application state and navigation. API envelope models use generated `json_serializable` mappers; versioned domain artifacts remain dynamic maps validated by the Python services. Shared media, dialogs, field editors and loading controls live under `util/` and `workspace/view/`.

`flutter analyze`, `flutter test` and `flutter build web --release` validate the client. Build output is ignored. The backend serves a compiled build from `frontend/build/web` with same-origin API URLs.
