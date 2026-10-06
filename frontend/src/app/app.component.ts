import { CommonModule } from '@angular/common';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Component, computed, inject, signal, OnDestroy } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom, timeout } from 'rxjs';
import { FaceCropperComponent } from './face-cropper.component';
import { EventPanelComponent } from './event-panel.component';
import { FunctionSettingsComponent } from './function-settings.component';
import { RecognitionLogsComponent } from './recognition-logs.component';
import { FaceTestComponent } from './face-test.component';

interface User { id: number; username: string; role: string; }
interface Auth { user: User; csrf_token: string; }
interface Camera {
  camera_id: number; name: string; description: string; rtsp_url: string;
  location: string; enabled: boolean; created_at: string; updated_at: string;
  source_type: 'rtsp' | 'mp4'; has_test_video: boolean; video_filename: string | null;
  can_view: boolean; can_operate: boolean;
}
interface CameraForm { name: string; description: string; rtsp_url: string; location: string; enabled: boolean; source_type: 'rtsp' | 'mp4'; }
interface Match {person_id: number; name: string; face_id: number; similarity: number; candidate?: boolean;}
interface ReferenceFace {id: number; quality: number; state: string; image_available: boolean; image_expires_at: string; embedding_expires_at: string;}
interface Person {id: number; name: string; description: string; enabled: boolean; sync_status: string; faces: ReferenceFace[];}
interface SearchResult {matches: Match[]; threshold: number;}
interface FaceStatus {
  status: string; quality: number; reasons: string[]; embedding_ready: boolean;
  face_size?: number[]; blur_score?: number; brightness?: number; yaw?: number; pitch?: number; roll?: number;
  best?: {quality: number; frame_id: number; captured_at: string; model_version: string};
  matches?: Match[];
  sample_count?: number;
  comparison?: {outcome: string; top_similarity: number | null; threshold: number};
}
interface AnalysisTrack {person_image?: {frame_id:number} | null; track_id: number; confidence: number; face?: FaceStatus; reid?: {status: string; embedding_ready: boolean; identity_assignment?: boolean};}
interface AnalysisStatus {
  camera_id: number; state: string; error_code?: string; source_type?: string; stream_session_id?: string;
  person_detection_enabled?: boolean; face_detection_enabled?: boolean;
  person_detection_fps?: number; face_detection_fps?: number;
  detection_fps?: number; capture_fps?: number; processed_frames?: number; dropped_frames?: number;
  latency_p95_ms?: number; resolution?: number[]; result?: {tracks: AnalysisTrack[]; person_tracks?: AnalysisTrack[]; face_tracks?: AnalysisTrack[]; independent_detection?: boolean; detection_confidence?: number; detection_mode?: string; face_detection_threshold?: number; min_face_size?: number};
  face_analysis_fps?: number; face_roi_fps?: number; face_counts?: {embeddings_created?: number; faces_detected?: number};
  actual_device?: string; reconnect_attempts?: number; reconnects?: number;
  loop?: boolean;
  next_retry_seconds?: number | null; last_frame_at?: string; last_frame_age_seconds?: number;
}
interface SystemStatus {
  checked_at: string;
  services: { mariadb: { status: string }; qdrant: { status: string } };
  gpu: { status: string; gpu_name?: string; actual_device?: string; checked_at?: string; cuda_node_count?: number };
  worker: {status: string; detector?: {model: string; actual_device: string}; resources?: {gpu_reserved_mb: number}};
}

@Component({
  selector: 'app-root', standalone: true, imports: [CommonModule, FormsModule, FaceCropperComponent, EventPanelComponent, FunctionSettingsComponent, RecognitionLogsComponent, FaceTestComponent],
  templateUrl: './app.component.html'
})
export class AppComponent implements OnDestroy {
  private http = inject(HttpClient);
  private csrf = '';
  user = signal<User | null>(null);
  loading = signal(true);
  busy = signal(false);
  backend = signal('Checking');
  view = signal<'dashboard' | 'cameras' | 'persons' | 'person-editor' | 'live' | 'logs' | 'settings' | 'face-test'>('dashboard');
  controlsRefresh = signal(0);
  status = signal<SystemStatus | null>(null);
  cameras = signal<Camera[]>([]);
  dashboardStatuses = signal<Record<number, AnalysisStatus>>({});
  private dashboardPendingSession: number | null = null;
  viewableCameras = computed(() => this.cameras().filter(camera => camera.can_view));
  private camerasLoaded = false;
  private liveRouteId: number | null = null;
  private liveVersion = 0;
  private sessionVersion = 0;
  persons = signal<Person[]>([]);
  selectedPerson = signal<Person | null>(null);
  personLoading = signal(false);
  personLoadFailed = signal(false);
  private personPageVersion = 0;
  private currentHash = window.location.hash;
  private routeChanged = () => {
    if (this.busy()) {
      window.history.replaceState(null, '', window.location.pathname + window.location.search + this.currentHash);
      return;
    }
    this.applyRoute();
  };
  personForm = {name: '', description: '', enabled: true};
  personEditingId: number | null = null;
  personPermissionUsername = '';
  cropFiles = signal<File[]>([]);
  cropFileCount = 0;
  searchResult = signal<SearchResult | null>(null);
  error = signal('');
  notice = signal('');
  username = '';
  password = '';
  editingId: number | null = null;
  cameraForm: CameraForm = this.emptyForm();
  selectedCamera = signal<Camera | null>(null);
  analysis = signal<AnalysisStatus | null>(null);
  previewFailed = signal(false);
  previewVersion = signal(0);
  analysisSource: 'rtsp' | 'mp4' = 'rtsp';
  loopVideo = true;
  analysisPersonEnabled = true;
  analysisFaceEnabled = true;
  private detectionChoices = new Map<number, {person: boolean; face: boolean}>();
  detectionSelectionChanged() {
    const id = this.selectedCamera()?.camera_id;
    if (id !== undefined) this.detectionChoices.set(id, {person:this.analysisPersonEnabled, face:this.analysisFaceEnabled});
  }
  private loadDetectionSelection(id: number) {
    const choice = this.detectionChoices.get(id);
    this.analysisPersonEnabled = choice?.person ?? true;
    this.analysisFaceEnabled = choice?.face ?? true;
    if (choice) return;
    const version = this.liveVersion;
    this.http.get<{values:{person_detection_enabled:boolean}}>('/api/function-settings').pipe(timeout(12000)).subscribe({
      next: settings => {
        if (version !== this.liveVersion || this.selectedCamera()?.camera_id !== id || this.detectionChoices.has(id) || this.activeAnalysis() || this.busy()) return;
        this.analysisPersonEnabled = settings.values.person_detection_enabled;
        this.analysisFaceEnabled = true;
      }, error: err => {if (err.status === 401 && version === this.liveVersion) this.clearSession();}
    });
  }
  private statusPending = false;
  private pollTimer = window.setInterval(() => {
    if (this.user() && this.selectedCamera() && this.view() === 'live') this.refreshAnalysis();
  }, 2000);
  private metadataTimer = window.setInterval(() => {
    if (this.user() && (this.view() === 'live' || this.view() === 'dashboard')) this.refresh();
  }, 30000);
  private dashboardTimer = window.setInterval(() => {
    if (this.user() && this.view() === 'dashboard') void this.refreshDashboardStatuses();
  }, 10000);
  ngOnDestroy() { window.clearInterval(this.dashboardTimer); window.clearInterval(this.pollTimer); window.clearInterval(this.metadataTimer); window.removeEventListener('hashchange', this.routeChanged); }

  constructor() {
    window.addEventListener('hashchange', this.routeChanged);
    this.http.get<{status: string}>('/api/health').pipe(timeout(6000)).subscribe({
      next: () => this.backend.set('Connected'), error: () => this.backend.set('Connection failed')
    });
    this.http.get<Auth>('/api/auth/me').pipe(timeout(6000)).subscribe({
      next: auth => { this.acceptAuth(auth); this.loading.set(false); },
      error: () => this.loading.set(false)
    });
  }

  emptyForm(): CameraForm { return {name: '', description: '', rtsp_url: '', location: '', enabled: true, source_type: 'rtsp'}; }
  headers() { return {'X-CSRF-Token': this.csrf}; }
  acceptAuth(auth: Auth) { this.sessionVersion++; this.csrf = auth.csrf_token; this.user.set(auth.user); this.refresh(); this.applyRoute(); }

  navigate(path: string) {
    if (this.busy()) return;
    if (path === 'live' && this.selectedCamera()) path = `live/${this.selectedCamera()!.camera_id}`;
    window.history.pushState(null, '', `#/${path}`);
    this.applyRoute();
  }

  private applyRoute() {
    this.currentHash = window.location.hash;
    if (!this.user()) return;
    this.resetPersonEditor(); this.error.set(''); this.notice.set('');
    this.liveVersion++; this.statusPending = false;
    window.scrollTo(0, 0);
    const path = this.currentHash.replace(/^#\/?/, '');
    const detail = /^persons\/([1-9]\d*)\/(edit|view)$/.exec(path);
    const live = /^live(?:\/([1-9]\d*))?$/.exec(path);
    this.liveRouteId = live?.[1] ? Number(live[1]) : null;
    if (path === 'persons/new' || detail) {
      this.view.set('person-editor');
      if (!detail) {
        if (this.user()?.role !== 'admin') {
          this.personLoadFailed.set(true); this.error.set('You do not have permission to register people.');
        }
        return;
      }
      this.personEditingId = Number(detail[1]);
      this.personLoading.set(true);
      const version = this.personPageVersion;
      this.http.get<Person>(`/api/persons/${this.personEditingId}`).pipe(timeout(8000)).subscribe({
        next: person => {
          if (version !== this.personPageVersion || !this.user()) return;
          this.selectedPerson.set(person);
          this.personForm = {name: person.name, description: person.description, enabled: person.enabled};
          this.personLoading.set(false);
        },
        error: err => {
          if (version !== this.personPageVersion || !this.user()) return;
          this.personLoading.set(false); this.personLoadFailed.set(true);
          if (err.status === 404) this.error.set('Person not found. Select another person from the list.');
          else this.handleError(err);
        }
      });
    } else {
      this.view.set(live ? 'live' : path === 'persons' || path === 'cameras' || path === 'logs' || path === 'settings' || path === 'face-test' ? path : 'dashboard');
      if (path === 'persons') this.refreshPersons();
      if (path === 'logs') this.refresh();
      if (this.view() === 'dashboard' && this.camerasLoaded) this.refresh();
      if (live && this.camerasLoaded) {
        // A camera may have been added in another tab since this list was loaded.
        if (this.liveRouteId === null || this.viewableCameras().some(camera => camera.camera_id === this.liveRouteId)) {
          this.restoreLiveCamera();
        }
        this.refresh();
      }
    }
  }

  pageTitle() {
    if (this.view() === 'person-editor') return this.user()?.role === 'admin'
      ? (this.personEditingId === null ? 'Add Person' : 'Edit Person') : 'Person Details';
    return {dashboard:'System Readiness', cameras:'Cameras', persons:'People', live:'Live Detector', logs:'Logs', settings:'Feature Settings', 'face-test':'Face Detection Test'}[this.view() as 'dashboard' | 'cameras' | 'persons' | 'live' | 'logs' | 'settings' | 'face-test'];
  }

  login() {
    if (this.busy()) return;
    this.busy.set(true); this.error.set('');
    this.http.post<Auth>('/api/auth/login', {username: this.username, password: this.password})
      .pipe(timeout(10000)).subscribe({
        next: auth => { this.password = ''; this.busy.set(false); this.acceptAuth(auth); },
        error: err => { this.password = ''; this.busy.set(false); this.error.set(
          err.status === 429 ? 'Too many login attempts. Please try again shortly.' : 'Check your credentials or the server connection.'); }
      });
  }

  logout() {
    if (this.busy()) return;
    this.http.post('/api/auth/logout', {}, {headers: this.headers()}).subscribe({
      next: () => this.clearSession(), error: () => this.error.set('Could not log out. Please try again.')
    });
  }

  clearSession() { this.dashboardStatuses.set({}); this.dashboardPendingSession = null; this.detectionChoices.clear(); this.analysisPersonEnabled = this.analysisFaceEnabled = true; this.sessionVersion++; this.liveVersion++; this.statusPending = false; this.camerasLoaded = false; this.resetPersonEditor(); this.user.set(null); this.csrf = ''; this.cameras.set([]); this.persons.set([]); this.status.set(null); this.selectedCamera.set(null); this.analysis.set(null); this.error.set(''); this.notice.set(''); }

  handleError(err: HttpErrorResponse) {
    if (err.status === 401) { this.clearSession(); this.error.set('Your session expired. Please log in again.'); }
    else this.error.set(err.status === 403 ? 'You do not have permission to perform this action.' : 'Could not process the request. Check the server connection and input values.');
  }

  refresh() {
    const version = this.sessionVersion;
    this.error.set('');
    this.refreshPersons();
    this.http.get<SystemStatus>('/api/system/status').pipe(timeout(8000)).subscribe({
      next: value => {if (version === this.sessionVersion && this.user()) this.status.set(value);},
      error: err => {if (version === this.sessionVersion) this.handleError(err);}
    });
    this.http.get<Camera[]>('/api/cameras').pipe(timeout(8000)).subscribe({
      next: value => {
        if (version !== this.sessionVersion || !this.user()) return;
        this.cameras.set(value);
        this.camerasLoaded = true;
        const selected = this.selectedCamera();
        if (selected) {
          this.selectedCamera.set(value.find(camera => camera.camera_id === selected.camera_id && camera.can_view) || null);
          if (!this.selectedCamera()) this.analysis.set(null);
        }
        if (this.view() === 'live') this.restoreLiveCamera();
        if (this.view() === 'dashboard') void this.refreshDashboardStatuses();
      }, error: err => {if (version === this.sessionVersion) this.handleError(err);}
    });
  }

  async refreshDashboardStatuses() {
    const version = this.sessionVersion;
    if (!this.user() || this.dashboardPendingSession !== null) return;
    this.dashboardPendingSession = version;
    const queue = this.cameras().filter(camera => camera.can_view);
    const states: Record<number, AnalysisStatus> = {};
    try {
      await Promise.all(Array.from({length: Math.min(4, queue.length)}, async () => {
        while (queue.length && version === this.sessionVersion && this.view() === 'dashboard') {
          const camera = queue.shift()!;
          try {
            states[camera.camera_id] = await firstValueFrom(this.http.get<AnalysisStatus>(
              `/api/cameras/${camera.camera_id}/status`).pipe(timeout(12000)));
          } catch (err) {
            const status = (err as HttpErrorResponse).status;
            if (status === 401 && version === this.sessionVersion) { this.clearSession(); return; }
            states[camera.camera_id] = {camera_id: camera.camera_id, state: status === 403 ? 'forbidden' : 'unavailable'};
          }
        }
      }));
      if (version === this.sessionVersion && this.user() && this.view() === 'dashboard') this.dashboardStatuses.set(states);
    } finally {
      if (this.dashboardPendingSession === version) this.dashboardPendingSession = null;
    }
  }

  dashboardState(camera: Camera) {
    return camera.can_view ? (this.dashboardStatuses()[camera.camera_id]?.state ?? '') : 'forbidden';
  }
  dashboardPeople(state: AnalysisStatus) {
    return state.result ? (state.result.person_tracks?.length ?? (state.result.detection_mode === 'person' ? state.result.tracks.length : 0)) : null;
  }
  refreshPage() {
    if (this.busy()) return;
    if (this.view() === 'logs' || this.view() === 'settings' || this.view() === 'face-test') {if (this.view() === 'logs') this.refresh(); this.controlsRefresh.update(value => value + 1); return;}
    if (this.view() === 'person-editor') this.applyRoute();
    else { this.refresh(); this.refreshAnalysis(); }
  }

  refreshPersons() {
    const version = this.sessionVersion;
    this.http.get<Person[]>('/api/persons').subscribe({next: value => {
      if (version !== this.sessionVersion || !this.user()) return;
      this.persons.set(value);
      const selected = this.selectedPerson();
      if (selected) this.selectedPerson.set(value.find(person => person.id === selected.id) || null);
    }, error: err => {if (version === this.sessionVersion) this.handleError(err);}});
  }
  openPersonPage(person: Person | null = null) {
    this.navigate(person ? `persons/${person.id}/${this.user()?.role === 'admin' ? 'edit' : 'view'}` : 'persons/new');
  }
  private resetPersonEditor() {
    this.personPageVersion++; this.personLoading.set(false); this.personLoadFailed.set(false);
    this.selectedPerson.set(null); this.personEditingId = null;
    this.personForm = {name: '', description: '', enabled: true};
    this.personPermissionUsername = ''; this.searchResult.set(null); this.cropFiles.set([]); this.cropFileCount = 0;
  }
  private acceptPerson(person: Person) {
    this.selectedPerson.set(person); this.personEditingId = person.id;
    this.personForm = {name: person.name, description: person.description, enabled: person.enabled};
    this.currentHash = `#/persons/${person.id}/edit`;
    window.history.replaceState(null, '', this.currentHash);
  }
  savePerson() {
    if (this.busy() || this.personLoading() || this.personLoadFailed() || this.user()?.role !== 'admin') return;
    if (!this.personForm.name.trim()) { this.error.set('Enter a person name.'); return; }
    this.busy.set(true); this.error.set('');
    const request = this.personEditingId === null
      ? this.http.post<Person>('/api/persons', this.personForm, {headers: this.headers()})
      : this.http.put<Person>(`/api/persons/${this.personEditingId}`, this.personForm, {headers: this.headers()});
    request.pipe(timeout(30000)).subscribe({next: person => {
      this.busy.set(false); this.acceptPerson(person);
      this.searchResult.set(null); this.refreshPersons();
      this.notice.set(person.sync_status === 'pending' ? 'Details saved. Retrying search synchronization.' : 'Person details saved. Add face photos.');
    }, error: err => { this.busy.set(false); this.handleError(err); }});
  }
  deletePerson(person: Person) {
    if (this.busy() || !window.confirm(`“${person.name}” Delete this person and all registered faces?`)) return;
    this.busy.set(true);
    this.http.delete(`/api/persons/${person.id}`, {headers: this.headers(), observe: 'response'}).subscribe({next: response => {
      this.busy.set(false); this.navigate('persons'); this.refreshPersons();
      this.notice.set(response.status === 202 ? 'Excluded from search. Retrying storage deletion.' : 'Person and registered faces deleted.');
    }, error: err => {this.busy.set(false); this.handleError(err);}});
  }
  referenceUrl(person: Person, face: ReferenceFace) {return `/api/persons/${person.id}/faces/${face.id}/image`;}
  referenceError(err: HttpErrorResponse) {
    const labels: Record<string,string> = {no_face:'No face found. Choose a photo with a clearly visible face.', multiple_faces:'Multiple faces found. Choose a photo containing only one person.', quality_rejected:'The face did not pass quality checks. Choose a large, clear, front-facing photo.', invalid_image:'Could not read the image. Choose a JPEG or PNG file.', image_dimensions_exceeded:'Choose an image with sides up to 4096 px and no more than 12 million pixels.'};
    if (labels[err.error?.detail?.code]) this.error.set(labels[err.error.detail.code]);
    else if (err.status === 413) this.error.set('Images must be 10 MB or less.');
    else if (err.status === 429) this.error.set('Another image is being processed. Please try again shortly.');
    else this.handleError(err);
  }
  uploadReferences(event: Event) {
    const input = event.target as HTMLInputElement;
    const files = Array.from(input.files || []);
    input.value = '';
    if (!files.length || this.busy() || this.user()?.role !== 'admin') return;
    this.error.set(''); this.notice.set('');
    if (files.some(file => !['image/jpeg','image/png'].includes(file.type) || file.size > 10 * 1024 ** 2)) {
      this.error.set('Choose a JPEG or PNG image up to 10 MB.'); return;
    }
    if (files.length > 20) {this.error.set('Select no more than 20 photos at a time.'); return;}
    this.cropFileCount = files.length; this.cropFiles.set(files);
  }
  skipReference() {
    if (this.busy()) return;
    this.cropFiles.update(files => files.slice(1)); this.error.set(''); this.notice.set('');
  }
  async saveCroppedReference(photo: Blob) {
    if (this.busy() || !this.cropFiles().length || this.user()?.role !== 'admin') return;
    if (this.personEditingId !== null && !this.selectedPerson()) {this.error.set('Reload person details before uploading photos.'); return;}
    if (!this.personForm.name.trim()) {this.error.set('Enter the person name before saving face images.'); return;}
    this.busy.set(true); this.error.set(''); this.notice.set('');
    let created = false;
    try {
      let person = this.selectedPerson();
      if (!person) {
        person = await firstValueFrom(this.http.post<Person>('/api/persons', this.personForm, {headers: this.headers()}).pipe(timeout(30000)));
        this.acceptPerson(person); created = true;
      }
      const face = await firstValueFrom(this.http.post<ReferenceFace>(`/api/persons/${person.id}/faces`, photo,
        {headers: {...this.headers(), 'Content-Type': 'image/jpeg'}}).pipe(timeout(45000)));
      this.selectedPerson.set({...person, faces: [...person.faces, face]});
      this.cropFiles.update(files => files.slice(1)); this.searchResult.set(null); this.refreshPersons();
      this.notice.set(this.cropFiles().length ? 'Cropped face saved. Select the region for the next image.' : 'Cropped face saved.');
    } catch (err) {
      this.referenceError(err as HttpErrorResponse);
      if (created && this.user()) this.error.update(message => message + ' Person details saved. Adjust the face region and try saving again.');
    } finally {this.busy.set(false);}
  }
  deleteReference(person: Person, face: ReferenceFace) {
    if (this.busy() || !window.confirm('Delete this registered face?')) return;
    this.busy.set(true); this.error.set('');
    this.http.delete(`/api/persons/${person.id}/faces/${face.id}`, {headers:this.headers(), observe:'response'}).subscribe({next: response => {
      this.busy.set(false); this.refreshPersons(); this.searchResult.set(null); this.notice.set(response.status === 202 ? 'Excluded from search. Retrying deletion.' : 'Registered face deleted.');
    }, error: err => { this.busy.set(false); this.handleError(err); }});
  }
  searchReference(event: Event) {
    const input = event.target as HTMLInputElement, file = input.files?.[0];
    if (!file || this.busy()) return;
    this.busy.set(true); this.error.set(''); this.notice.set(''); this.searchResult.set(null);
    this.http.post<SearchResult>('/api/persons/search', file, {headers:{...this.headers(),'Content-Type':file.type}}).pipe(timeout(30000)).subscribe({
      next: result => {this.searchResult.set(result); this.busy.set(false); input.value='';},
      error: err => {this.busy.set(false); this.referenceError(err); input.value='';}
    });
  }
  setPersonAccess(canView: boolean) {
    const person = this.selectedPerson(); if (!person || !this.personPermissionUsername.trim() || this.busy()) return;
    this.busy.set(true); this.error.set('');
    this.http.put(`/api/persons/${person.id}/access`, {username:this.personPermissionUsername.trim(),can_view:canView}, {headers:this.headers()}).subscribe({next: () => {
      this.busy.set(false); this.notice.set(canView ? 'Access to person details and face images granted.' : 'Person access revoked.'); this.personPermissionUsername='';
    }, error: err => {this.busy.set(false); this.handleError(err);}});
  }
  retryReferences() {
    this.busy.set(true);
    this.http.post<{pending:number}>('/api/persons/maintenance/retry', {}, {headers:this.headers()}).pipe(timeout(60000)).subscribe({next: result => {
      this.busy.set(false); this.refreshPersons(); this.notice.set(result.pending ? `Retrying ${result.pending} search synchronization/deletion tasks.` : 'Registration and deletion changes applied.');
    }, error: err => {this.busy.set(false); this.handleError(err);}});
  }

  edit(camera: Camera) {
    this.editingId = camera.camera_id;
    this.cameraForm = {name: camera.name, description: camera.description, location: camera.location, enabled: camera.enabled, rtsp_url: '', source_type: camera.source_type};
    this.notice.set(camera.source_type === 'rtsp' ? 'Re-enter the camera RTSP address with its credentials. Saving stops analysis.' : 'Saving stops active analysis.');
  }
  cancelEdit() { this.editingId = null; this.cameraForm = this.emptyForm(); this.notice.set(''); }

  saveCamera() {
    if (this.busy()) return;
    if (!this.cameraForm.name.trim()) { this.error.set('Enter a camera name.'); return; }
    if (this.cameraForm.source_type === 'rtsp' && !this.cameraForm.rtsp_url.trim()) {
      this.error.set('Enter an RTSP address for the CCTV input.'); return;
    }
    this.busy.set(true); this.error.set('');
    const payload = {...this.cameraForm, rtsp_url:this.cameraForm.source_type === 'mp4' ? '' : this.cameraForm.rtsp_url};
    const request = this.editingId === null
      ? this.http.post<Camera>('/api/cameras', payload, {headers: this.headers()})
      : this.http.put<Camera>(`/api/cameras/${this.editingId}`, payload, {headers: this.headers()});
    request.pipe(timeout(10000)).subscribe({
      next: () => { this.busy.set(false); this.cancelEdit(); this.refresh(); this.notice.set('Camera details saved.'); },
      error: err => { this.busy.set(false); if (err.status === 409) this.error.set('A camera with this name already exists.'); else this.handleError(err); }
    });
  }

  deleteCamera(camera: Camera) {
    if (!window.confirm(`“${camera.name}” Delete this camera?`)) return;
    this.http.delete(`/api/cameras/${camera.camera_id}`, {headers: this.headers()}).subscribe({
      next: () => { this.cancelEdit(); this.refresh(); this.notice.set('Camera deleted.'); }, error: err => this.handleError(err)
    });
  }

  openLive(camera: Camera) {
    if (!camera.can_view || this.busy()) return;
    this.navigate(`live/${camera.camera_id}`);
  }
  private restoreLiveCamera() {
    const id = this.liveRouteId ?? this.selectedCamera()?.camera_id;
    const camera = this.cameras().find(value => value.camera_id === id && value.can_view);
    if (!camera) {
      this.selectedCamera.set(null); this.analysis.set(null);
      if (this.liveRouteId !== null) this.error.set('Camera not found or video access denied. Select another camera from the list.');
      return;
    }
    if (this.selectedCamera()?.camera_id !== camera.camera_id) {
      this.liveVersion++; this.statusPending = false;
      this.selectedCamera.set(camera); this.analysis.set(null); this.previewFailed.set(false);
      this.analysisSource = camera.source_type;
      this.loadDetectionSelection(camera.camera_id);
    }
    this.refreshAnalysis();
  }
  selectLive(id: string | number) {
    const camera = this.cameras().find(value => value.camera_id === Number(id));
    if (camera) this.openLive(camera);
  }
  refreshAnalysis() {
    const camera = this.selectedCamera();
    if (!camera || this.statusPending) return;
    const version = this.liveVersion;
    this.statusPending = true;
    this.http.get<AnalysisStatus>(`/api/cameras/${camera.camera_id}/status`).pipe(timeout(12000)).subscribe({
      next: value => {
        if (version !== this.liveVersion || !this.user()) return;
        if (this.selectedCamera()?.camera_id === camera.camera_id) {
          const previous = this.analysis();
          if (value.stream_session_id && value.stream_session_id !== previous?.stream_session_id) {
            this.previewVersion.update(version => version + 1); this.previewFailed.set(false);
          }
          if (value.state === 'running' && previous?.state !== 'running') this.previewFailed.set(false);
          this.analysis.set(value);
          if ((this.activeAnalysis() || !this.detectionChoices.has(camera.camera_id)) && value.person_detection_enabled !== undefined && value.face_detection_enabled !== undefined) {
            this.analysisPersonEnabled = value.person_detection_enabled;
            this.analysisFaceEnabled = value.face_detection_enabled;
            this.detectionSelectionChanged();
          }
          if (this.activeAnalysis() && (value.source_type === 'mp4' || value.source_type === 'rtsp')) {
            this.analysisSource = value.source_type; this.loopVideo = value.loop ?? this.loopVideo;
          }
        }
        this.statusPending = false;
      },
      error: err => {
        if (version !== this.liveVersion || !this.user()) return;
        this.statusPending = false;
        if (err.status === 403 || err.status === 404) {this.selectedCamera.set(null); this.analysis.set(null);}
        this.handleError(err);
      }
    });
  }
  activeAnalysis() { return ['opening', 'running', 'reconnecting', 'draining', 'stopping'].includes(this.analysis()?.state || ''); }
  startAnalysis() {
    const camera = this.selectedCamera(); if (!camera?.can_operate || this.busy() || this.activeAnalysis() || (this.analysisSource === 'mp4' && !this.analysisPersonEnabled && !this.analysisFaceEnabled)) return;
    this.detectionSelectionChanged();
    this.busy.set(true); this.error.set(''); this.notice.set(''); this.previewFailed.set(false);
    this.http.post<AnalysisStatus>(`/api/cameras/${camera.camera_id}/start`, {source_type:this.analysisSource, loop:this.loopVideo, person_detection_enabled:this.analysisSource === 'mp4' ? this.analysisPersonEnabled : true, face_detection_enabled:this.analysisSource === 'mp4' ? this.analysisFaceEnabled : false}, {headers:this.headers()})
      .pipe(timeout(15000)).subscribe({
        next: value => { this.analysis.set(value); this.previewVersion.update(value => value + 1); this.busy.set(false); this.refreshAnalysis(); },
        error: err => { this.busy.set(false); this.handleError(err); }
      });
  }
  stopAnalysis() {
    const camera = this.selectedCamera(); if (!camera?.can_operate || this.busy()) return;
    this.busy.set(true);
    this.http.post<AnalysisStatus>(`/api/cameras/${camera.camera_id}/stop`, {}, {headers:this.headers()}).pipe(timeout(30000)).subscribe({
      next: value => { this.analysis.set(value); this.busy.set(false); this.notice.set('Analysis stopped.'); },
      error: err => { this.busy.set(false); this.handleError(err); }
    });
  }
  uploadVideo(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0], camera = this.selectedCamera();
    if (!file || !camera || this.busy()) return;
    if (!file.name.toLowerCase().endsWith('.mp4') || file.size > 200 * 1024 * 1024) {
      this.error.set('Choose an MP4 file up to 200 MB.'); input.value = ''; return;
    }
    this.busy.set(true); this.error.set('');
    this.http.put<Camera>(`/api/cameras/${camera.camera_id}/video`, file, {params:{filename:file.name}, headers:{...this.headers(), 'Content-Type':'video/mp4'}}).pipe(timeout(120000)).subscribe({
      next: value => { this.selectedCamera.set(value); this.analysisSource = 'mp4'; this.busy.set(false); this.notice.set('Test video uploaded. Click Start Analysis.'); this.refresh(); input.value = ''; },
      error: err => { this.busy.set(false); this.handleError(err); input.value = ''; }
    });
  }
  personTracks() {return this.analysis()?.result?.person_tracks ?? [];}
  faceTracks() {return this.analysis()?.face_detection_enabled === false ? [] : this.analysis()?.result?.face_tracks ?? this.analysis()?.result?.tracks ?? [];}
  personUrl(track: AnalysisTrack) {
    return `/api/cameras/${this.selectedCamera()?.camera_id}/people/${track.track_id}?stream_session_id=${this.analysis()?.stream_session_id}&v=${track.person_image?.frame_id}`;
  }
  faceUrl(track: AnalysisTrack) {
    return `/api/cameras/${this.selectedCamera()?.camera_id}/faces/${track.track_id}?stream_session_id=${this.analysis()?.stream_session_id}&v=${track.face?.best?.frame_id}`;
  }
  candidateCount() {return (this.analysis()?.result?.tracks || []).filter(track => track.face?.matches?.length).length;}
  faceLabel(face?: FaceStatus) {
    if (face?.embedding_ready) return 'Face features ready';
    const labels: Record<string,string> = {pending:'Waiting for face analysis', no_face:'No face visible', ambiguous:'Face association pending', rejected:'Below quality threshold', capacity:'Waiting for face analysis'};
    return labels[face?.status || ''] || 'Waiting for face analysis';
  }
  faceReasons(face?: FaceStatus) {
    const labels: Record<string,string> = {no_face:'No face detected', multiple_faces:'Multiple faces detected', face_too_small:'Face too small', face_clipped:'Face extends beyond the frame', invalid_face_box:'Invalid face region', blurred:'Blurry video', too_dark:'Too dark', too_bright:'Too bright', low_confidence:'Low detection confidence', landmark_geometry:'landmark unstable or possibly occluded', pose_exceeded:'Face pose exceeds limits', pose_unavailable:'Pose unavailable', quality_below_threshold:'Low overall quality', cache_capacity:'Analysis capacity limit'};
    return (face?.reasons || []).map(reason => labels[reason] || reason).join(' · ');
  }
  previewUrl() { return `/api/cameras/${this.selectedCamera()?.camera_id}/preview?v=${this.previewVersion()}`; }
  reconnectPreview() { this.previewFailed.set(false); this.previewVersion.update(value => value + 1); }
  analysisLabel(state: string | undefined = this.analysis()?.state) {
    const labels: Record<string,string> = {stopped:'Stopped', opening:'Connecting to video', running:'Analyzing', reconnecting:'Reconnecting automatically', draining:'Processing final frame', stopping:'Stopping', ended:'Playback complete', error:'Connection or analysis failed', unavailable:'Status unavailable', forbidden:'No view permission'};
    return labels[state || ''] || 'Checking status';
  }
  analysisError() {
    if (this.analysis()?.state === 'reconnecting') return '';
    const labels: Record<string,string> = {source_open_failed:'Could not open the video. Check the camera credentials and network.', source_read_failed:'Video reception stopped. Restart analysis.', source_resolution_exceeded:'Maximum supported input resolution: 3840×2160.', capture_failed:'Video reception failed.', inference_failed:'Video analysis failed. Check the analysis service.', video_seek_failed:'Could not replay the test video.'};
    return labels[this.analysis()?.error_code || ''] || '';
  }
  reconnectLabel() {
    const seconds = this.analysis()?.next_retry_seconds;
    return seconds === null || seconds === undefined
      ? 'Retrying the camera connection.'
      : `Reconnecting in about ${Math.ceil(seconds)} seconds.`;
  }
  serviceLabel(value: string | undefined) { return value === 'ok' ? 'Healthy' : value === 'unavailable' ? 'Connection failed' : 'Checking'; }
  gpuLabel() {
    const gpu = this.status()?.gpu;
    if (gpu?.status === 'passed') return gpu.actual_device === 'cuda' ? 'CUDA Validation passed' : 'CPU Validation passed';
    if (gpu?.status === 'cpu_fallback') return 'CPU fallback';
    return gpu?.status === 'failed' ? 'Validation failed' : 'Not yet validated';
  }
}
