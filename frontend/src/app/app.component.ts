import { CommonModule } from '@angular/common';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Component, computed, inject, signal, OnDestroy } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom, timeout } from 'rxjs';
import { FaceCropperComponent } from './face-cropper.component';
import { EventPanelComponent } from './event-panel.component';

interface User { id: number; username: string; role: string; }
interface Auth { user: User; csrf_token: string; }
interface Camera {
  camera_id: number; name: string; description: string; rtsp_url: string;
  location: string; enabled: boolean; created_at: string; updated_at: string;
  source_type: 'rtsp' | 'mp4'; has_test_video: boolean;
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
}
interface AnalysisTrack {track_id: number; confidence: number; face?: FaceStatus;}
interface AnalysisStatus {
  camera_id: number; state: string; error_code?: string; source_type?: string; stream_session_id?: string;
  detection_fps?: number; capture_fps?: number; processed_frames?: number; dropped_frames?: number;
  latency_p95_ms?: number; resolution?: number[]; result?: {tracks: AnalysisTrack[]};
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
  selector: 'app-root', standalone: true, imports: [CommonModule, FormsModule, FaceCropperComponent, EventPanelComponent],
  templateUrl: './app.component.html'
})
export class AppComponent implements OnDestroy {
  private http = inject(HttpClient);
  private csrf = '';
  user = signal<User | null>(null);
  loading = signal(true);
  busy = signal(false);
  backend = signal('확인 중');
  view = signal<'dashboard' | 'cameras' | 'persons' | 'person-editor' | 'live'>('dashboard');
  status = signal<SystemStatus | null>(null);
  cameras = signal<Camera[]>([]);
  cameraSearch = signal('');
  viewableCameras = computed(() => this.cameras().filter(camera => camera.can_view));
  liveCameras = computed(() => {
    const query = this.cameraSearch().trim().toLocaleLowerCase();
    return this.viewableCameras().filter(camera => !query
      || `${camera.name} ${camera.location}`.toLocaleLowerCase().includes(query));
  });
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
  permissionUsername = '';
  permissionOperate = false;
  private statusPending = false;
  private pollTimer = window.setInterval(() => {
    if (this.user() && this.selectedCamera() && this.view() === 'live') this.refreshAnalysis();
  }, 2000);
  private metadataTimer = window.setInterval(() => {
    if (this.user() && this.view() === 'live') this.refresh();
  }, 30000);
  ngOnDestroy() { window.clearInterval(this.pollTimer); window.clearInterval(this.metadataTimer); window.removeEventListener('hashchange', this.routeChanged); }

  constructor() {
    window.addEventListener('hashchange', this.routeChanged);
    this.http.get<{status: string}>('/api/health').pipe(timeout(6000)).subscribe({
      next: () => this.backend.set('연결됨'), error: () => this.backend.set('연결 실패')
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
          this.personLoadFailed.set(true); this.error.set('인물을 등록할 권한이 없습니다.');
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
          if (err.status === 404) this.error.set('인물을 찾을 수 없습니다. 목록에서 다시 선택해 주세요.');
          else this.handleError(err);
        }
      });
    } else {
      this.view.set(live ? 'live' : path === 'persons' || path === 'cameras' ? path : 'dashboard');
      if (path === 'persons') this.refreshPersons();
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
      ? (this.personEditingId === null ? '인물 추가' : '인물 수정') : '인물 정보';
    return {dashboard:'시스템 준비 상태', cameras:'카메라 관리', persons:'인물 관리', live:'Live Search'}[this.view() as 'dashboard' | 'cameras' | 'persons' | 'live'];
  }

  login() {
    if (this.busy()) return;
    this.busy.set(true); this.error.set('');
    this.http.post<Auth>('/api/auth/login', {username: this.username, password: this.password})
      .pipe(timeout(10000)).subscribe({
        next: auth => { this.password = ''; this.busy.set(false); this.acceptAuth(auth); },
        error: err => { this.password = ''; this.busy.set(false); this.error.set(
          err.status === 429 ? '로그인 시도가 많습니다. 잠시 후 다시 시도해 주세요.' : '계정 정보 또는 서버 연결을 확인해 주세요.'); }
      });
  }

  logout() {
    if (this.busy()) return;
    this.http.post('/api/auth/logout', {}, {headers: this.headers()}).subscribe({
      next: () => this.clearSession(), error: () => this.error.set('로그아웃하지 못했습니다. 다시 시도해 주세요.')
    });
  }

  clearSession() { this.sessionVersion++; this.liveVersion++; this.statusPending = false; this.camerasLoaded = false; this.resetPersonEditor(); this.user.set(null); this.csrf = ''; this.cameras.set([]); this.persons.set([]); this.status.set(null); this.selectedCamera.set(null); this.analysis.set(null); this.cameraSearch.set(''); this.error.set(''); this.notice.set(''); }

  handleError(err: HttpErrorResponse) {
    if (err.status === 401) { this.clearSession(); this.error.set('세션이 만료되었습니다. 다시 로그인해 주세요.'); }
    else this.error.set(err.status === 403 ? '이 작업을 수행할 권한이 없습니다.' : '요청을 처리하지 못했습니다. 서버 연결과 입력값을 확인해 주세요.');
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
      }, error: err => {if (version === this.sessionVersion) this.handleError(err);}
    });
  }

  refreshPage() {
    if (this.busy()) return;
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
    if (!this.personForm.name.trim()) { this.error.set('인물 이름을 입력해 주세요.'); return; }
    this.busy.set(true); this.error.set('');
    const request = this.personEditingId === null
      ? this.http.post<Person>('/api/persons', this.personForm, {headers: this.headers()})
      : this.http.put<Person>(`/api/persons/${this.personEditingId}`, this.personForm, {headers: this.headers()});
    request.pipe(timeout(30000)).subscribe({next: person => {
      this.busy.set(false); this.acceptPerson(person);
      this.searchResult.set(null); this.refreshPersons();
      this.notice.set(person.sync_status === 'pending' ? '정보를 저장했습니다. 검색 반영을 재시도 중입니다.' : '인물 정보를 저장했습니다. 얼굴 사진을 추가하세요.');
    }, error: err => { this.busy.set(false); this.handleError(err); }});
  }
  deletePerson(person: Person) {
    if (this.busy() || !window.confirm(`“${person.name}” 인물과 등록 얼굴을 삭제할까요?`)) return;
    this.busy.set(true);
    this.http.delete(`/api/persons/${person.id}`, {headers: this.headers(), observe: 'response'}).subscribe({next: response => {
      this.busy.set(false); this.navigate('persons'); this.refreshPersons();
      this.notice.set(response.status === 202 ? '검색에서 제외했습니다. 저장소 삭제를 재시도 중입니다.' : '인물과 등록 얼굴을 삭제했습니다.');
    }, error: err => {this.busy.set(false); this.handleError(err);}});
  }
  referenceUrl(person: Person, face: ReferenceFace) {return `/api/persons/${person.id}/faces/${face.id}/image`;}
  referenceError(err: HttpErrorResponse) {
    const labels: Record<string,string> = {no_face:'얼굴을 찾지 못했습니다. 얼굴이 선명한 사진을 선택해 주세요.', multiple_faces:'여러 얼굴이 보입니다. 한 사람만 있는 사진을 선택해 주세요.', quality_rejected:'얼굴 품질 기준을 통과하지 못했습니다. 크고 선명한 정면 사진을 선택해 주세요.', invalid_image:'읽을 수 없는 이미지입니다. JPEG 또는 PNG 파일을 선택해 주세요.', image_dimensions_exceeded:'사진은 가로·세로 4096 px 이하, 1200만 픽셀 이하로 선택해 주세요.'};
    if (labels[err.error?.detail?.code]) this.error.set(labels[err.error.detail.code]);
    else if (err.status === 413) this.error.set('사진은 10 MB 이하로 선택해 주세요.');
    else if (err.status === 429) this.error.set('다른 사진을 처리 중입니다. 잠시 후 다시 시도해 주세요.');
    else this.handleError(err);
  }
  uploadReferences(event: Event) {
    const input = event.target as HTMLInputElement;
    const files = Array.from(input.files || []);
    input.value = '';
    if (!files.length || this.busy() || this.user()?.role !== 'admin') return;
    this.error.set(''); this.notice.set('');
    if (files.some(file => !['image/jpeg','image/png'].includes(file.type) || file.size > 10 * 1024 ** 2)) {
      this.error.set('10 MB 이하 JPEG 또는 PNG 사진을 선택해 주세요.'); return;
    }
    if (files.length > 20) {this.error.set('한 번에 최대 20장까지 선택해 주세요.'); return;}
    this.cropFileCount = files.length; this.cropFiles.set(files);
  }
  skipReference() {
    if (this.busy()) return;
    this.cropFiles.update(files => files.slice(1)); this.error.set(''); this.notice.set('');
  }
  async saveCroppedReference(photo: Blob) {
    if (this.busy() || !this.cropFiles().length || this.user()?.role !== 'admin') return;
    if (this.personEditingId !== null && !this.selectedPerson()) {this.error.set('인물 정보를 다시 불러온 다음 사진을 등록해 주세요.'); return;}
    if (!this.personForm.name.trim()) {this.error.set('인물 이름을 입력한 다음 얼굴 사진을 저장해 주세요.'); return;}
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
      this.notice.set(this.cropFiles().length ? '크롭한 얼굴 사진을 저장했습니다. 다음 사진의 영역을 선택해 주세요.' : '크롭한 얼굴 사진을 저장했습니다.');
    } catch (err) {
      this.referenceError(err as HttpErrorResponse);
      if (created && this.user()) this.error.update(message => message + ' 인물 정보는 저장되었습니다. 얼굴 영역을 조정해 다시 저장해 주세요.');
    } finally {this.busy.set(false);}
  }
  deleteReference(person: Person, face: ReferenceFace) {
    if (this.busy() || !window.confirm('이 등록 얼굴을 삭제할까요?')) return;
    this.busy.set(true); this.error.set('');
    this.http.delete(`/api/persons/${person.id}/faces/${face.id}`, {headers:this.headers(), observe:'response'}).subscribe({next: response => {
      this.busy.set(false); this.refreshPersons(); this.searchResult.set(null); this.notice.set(response.status === 202 ? '검색에서 제외했습니다. 삭제를 재시도 중입니다.' : '등록 얼굴을 삭제했습니다.');
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
      this.busy.set(false); this.notice.set(canView ? '인물 정보와 얼굴 사진 접근을 허용했습니다.' : '인물 접근 권한을 해제했습니다.'); this.personPermissionUsername='';
    }, error: err => {this.busy.set(false); this.handleError(err);}});
  }
  retryReferences() {
    this.busy.set(true);
    this.http.post<{pending:number}>('/api/persons/maintenance/retry', {}, {headers:this.headers()}).pipe(timeout(60000)).subscribe({next: result => {
      this.busy.set(false); this.refreshPersons(); this.notice.set(result.pending ? `${result.pending}건의 검색 반영·삭제 작업을 재시도 중입니다.` : '등록 및 삭제 작업을 반영했습니다.');
    }, error: err => {this.busy.set(false); this.handleError(err);}});
  }

  edit(camera: Camera) {
    this.editingId = camera.camera_id;
    this.cameraForm = {name: camera.name, description: camera.description, location: camera.location, enabled: camera.enabled, rtsp_url: '', source_type: camera.source_type};
    this.notice.set(camera.source_type === 'rtsp' ? '수정할 카메라의 RTSP 주소를 계정 정보와 함께 다시 입력해 주세요. 저장하면 분석이 중지됩니다.' : '저장하면 진행 중인 분석이 중지됩니다.');
  }
  cancelEdit() { this.editingId = null; this.cameraForm = this.emptyForm(); this.notice.set(''); }

  saveCamera() {
    if (this.busy()) return;
    if (!this.cameraForm.name.trim()) { this.error.set('카메라 이름을 입력해 주세요.'); return; }
    if (this.cameraForm.source_type === 'rtsp' && !this.cameraForm.rtsp_url.trim()) {
      this.error.set('CCTV 입력에서는 RTSP 주소를 입력해 주세요.'); return;
    }
    this.busy.set(true); this.error.set('');
    const payload = {...this.cameraForm, rtsp_url:this.cameraForm.source_type === 'mp4' ? '' : this.cameraForm.rtsp_url};
    const request = this.editingId === null
      ? this.http.post<Camera>('/api/cameras', payload, {headers: this.headers()})
      : this.http.put<Camera>(`/api/cameras/${this.editingId}`, payload, {headers: this.headers()});
    request.pipe(timeout(10000)).subscribe({
      next: () => { this.busy.set(false); this.cancelEdit(); this.refresh(); this.notice.set('카메라 정보를 저장했습니다.'); },
      error: err => { this.busy.set(false); if (err.status === 409) this.error.set('같은 이름의 카메라가 이미 있습니다.'); else this.handleError(err); }
    });
  }

  deleteCamera(camera: Camera) {
    if (!window.confirm(`“${camera.name}” 카메라를 삭제할까요?`)) return;
    this.http.delete(`/api/cameras/${camera.camera_id}`, {headers: this.headers()}).subscribe({
      next: () => { this.cancelEdit(); this.refresh(); this.notice.set('카메라를 삭제했습니다.'); }, error: err => this.handleError(err)
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
      if (this.liveRouteId !== null) this.error.set('카메라를 찾을 수 없거나 영상 접근 권한이 없습니다. 목록에서 다시 선택해 주세요.');
      return;
    }
    if (this.selectedCamera()?.camera_id !== camera.camera_id) {
      this.liveVersion++; this.statusPending = false;
      this.selectedCamera.set(camera); this.analysis.set(null); this.previewFailed.set(false);
      this.analysisSource = camera.source_type;
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
    const camera = this.selectedCamera(); if (!camera?.can_operate || this.busy()) return;
    this.busy.set(true); this.error.set(''); this.notice.set(''); this.previewFailed.set(false);
    this.http.post<AnalysisStatus>(`/api/cameras/${camera.camera_id}/start`, {source_type:this.analysisSource, loop:this.loopVideo}, {headers:this.headers()})
      .pipe(timeout(15000)).subscribe({
        next: value => { this.analysis.set(value); this.previewVersion.update(value => value + 1); this.busy.set(false); this.refreshAnalysis(); },
        error: err => { this.busy.set(false); this.handleError(err); }
      });
  }
  stopAnalysis() {
    const camera = this.selectedCamera(); if (!camera?.can_operate || this.busy()) return;
    this.busy.set(true);
    this.http.post<AnalysisStatus>(`/api/cameras/${camera.camera_id}/stop`, {}, {headers:this.headers()}).pipe(timeout(30000)).subscribe({
      next: value => { this.analysis.set(value); this.busy.set(false); this.notice.set('분석을 중지했습니다.'); },
      error: err => { this.busy.set(false); this.handleError(err); }
    });
  }
  uploadVideo(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0], camera = this.selectedCamera();
    if (!file || !camera || this.busy()) return;
    if (!file.name.toLowerCase().endsWith('.mp4') || file.size > 200 * 1024 * 1024) {
      this.error.set('200 MB 이하의 MP4 파일을 선택해 주세요.'); input.value = ''; return;
    }
    this.busy.set(true); this.error.set('');
    this.http.put<Camera>(`/api/cameras/${camera.camera_id}/video`, file, {headers:{...this.headers(), 'Content-Type':'video/mp4'}}).pipe(timeout(120000)).subscribe({
      next: value => { this.selectedCamera.set(value); this.analysisSource = 'mp4'; this.busy.set(false); this.notice.set('시험 영상을 업로드했습니다. 분석 시작을 눌러 주세요.'); this.refresh(); input.value = ''; },
      error: err => { this.busy.set(false); this.handleError(err); input.value = ''; }
    });
  }
  faceUrl(track: AnalysisTrack) {
    return `/api/cameras/${this.selectedCamera()?.camera_id}/faces/${track.track_id}?stream_session_id=${this.analysis()?.stream_session_id}&v=${track.face?.best?.frame_id}`;
  }
  candidateCount() {return (this.analysis()?.result?.tracks || []).filter(track => track.face?.matches?.length).length;}
  faceLabel(face?: FaceStatus) {
    if (face?.embedding_ready) return '얼굴 특징 준비됨';
    const labels: Record<string,string> = {pending:'얼굴 검사 대기', no_face:'얼굴이 보이지 않음', ambiguous:'얼굴 연결 보류', rejected:'품질 기준 미달', capacity:'얼굴 검사 대기'};
    return labels[face?.status || ''] || '얼굴 검사 대기';
  }
  faceReasons(face?: FaceStatus) {
    const labels: Record<string,string> = {no_face:'얼굴 미검출', multiple_faces:'여러 얼굴 검출', face_too_small:'얼굴이 작음', face_clipped:'얼굴이 화면 밖으로 잘림', invalid_face_box:'얼굴 영역 오류', blurred:'흐린 영상', too_dark:'너무 어두움', too_bright:'너무 밝음', low_confidence:'탐지 신뢰도 부족', landmark_geometry:'landmark 불안정 또는 가림 가능성', pose_exceeded:'얼굴 각도 기준 초과', pose_unavailable:'자세 측정 불가', quality_below_threshold:'종합 품질 부족', cache_capacity:'검사 용량 제한'};
    return (face?.reasons || []).map(reason => labels[reason] || reason).join(' · ');
  }
  previewUrl() { return `/api/cameras/${this.selectedCamera()?.camera_id}/preview?v=${this.previewVersion()}`; }
  reconnectPreview() { this.previewFailed.set(false); this.previewVersion.update(value => value + 1); }
  analysisLabel() {
    const labels: Record<string,string> = {stopped:'중지됨', opening:'영상 연결 중', running:'분석 중', reconnecting:'자동 재연결 중', draining:'마지막 프레임 처리 중', stopping:'중지 중', ended:'영상 재생 완료', error:'연결 또는 분석 실패'};
    return labels[this.analysis()?.state || ''] || '상태 확인 중';
  }
  analysisError() {
    if (this.analysis()?.state === 'reconnecting') return '';
    const labels: Record<string,string> = {source_open_failed:'영상을 열지 못했습니다. 카메라 접속 정보와 네트워크를 확인하세요.', source_read_failed:'영상 수신이 끊겼습니다. 분석을 다시 시작하세요.', source_resolution_exceeded:'지원하는 최대 입력 해상도는 3840×2160입니다.', capture_failed:'영상 수신에 실패했습니다.', inference_failed:'영상 분석에 실패했습니다. 분석 서비스를 확인하세요.', video_seek_failed:'시험 영상을 다시 재생하지 못했습니다.'};
    return labels[this.analysis()?.error_code || ''] || '';
  }
  reconnectLabel() {
    const seconds = this.analysis()?.next_retry_seconds;
    return seconds === null || seconds === undefined
      ? '카메라 연결을 다시 시도하고 있습니다.'
      : `약 ${Math.ceil(seconds)}초 후 다시 연결합니다.`;
  }
  setCameraAccess(canView: boolean) {
    const camera = this.selectedCamera(); if (!camera || !this.permissionUsername.trim()) return;
    this.http.put(`/api/cameras/${camera.camera_id}/access`, {username:this.permissionUsername.trim(), can_view:canView, can_operate:canView && this.permissionOperate}, {headers:this.headers()}).subscribe({
      next: () => { this.notice.set(canView ? '카메라 영상 접근 권한을 저장했습니다.' : '카메라 영상 접근 권한을 해제했습니다.'); this.permissionUsername = ''; },
      error: err => this.handleError(err)
    });
  }

  serviceLabel(value: string | undefined) { return value === 'ok' ? '정상' : value === 'unavailable' ? '연결 실패' : '확인 중'; }
  gpuLabel() {
    const gpu = this.status()?.gpu;
    if (gpu?.status === 'passed') return gpu.actual_device === 'cuda' ? 'CUDA 검증 통과' : 'CPU 검증 통과';
    if (gpu?.status === 'cpu_fallback') return 'CPU로 대체 실행';
    return gpu?.status === 'failed' ? '검증 실패' : '아직 검증되지 않음';
  }
}
