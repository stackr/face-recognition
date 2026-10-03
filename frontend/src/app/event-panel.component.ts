import {CommonModule} from '@angular/common';
import {HttpClient} from '@angular/common/http';
import {Component, DestroyRef, EventEmitter, Input, OnDestroy, OnInit, Output, computed, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {FormsModule} from '@angular/forms';
import {firstValueFrom, timeout} from 'rxjs';

export interface MatchEvent {
  type: 'person_match'; event_id: number; change_id: number; camera_id: number; camera_name: string;
  person_id: number; person_name: string; stream_session_id: string; track_id: number;
  timestamp: string; face_similarity: number; face_quality: number;
  status: 'candidate' | 'confirmed' | 'rejected'; thumbnail_url: string | null; frame_url: string | null;
  can_review: boolean; can_delete: boolean;
  video_clip_url?: string | null; clip_state?: string; clip_error?: string | null;
  clip_details?: {partial?: boolean; before_seconds?: number; after_seconds?: number};
}
export interface EventDeletion {type: 'event_deleted'; event_id: number; change_id: number;}
export type EventChange = MatchEvent | EventDeletion;
export interface EventPage {items: EventChange[]; next_cursor: number; change_cursor?: number; has_more: boolean;}
export interface ReferencePerson {
  id: number; faces: {id: number; quality: number; state: string; image_available: boolean}[];
}
export interface EventFilters {camera: 'selected' | 'all'; person: string; status: string;}

export function filterEvents(rows: MatchEvent[], filters: EventFilters, cameraId: number | null) {
  const name = filters.person.trim().toLocaleLowerCase();
  return rows.filter(event => (filters.camera === 'all' || cameraId === null || event.camera_id === cameraId)
    && (!name || event.person_name.toLocaleLowerCase().includes(name))
    && (filters.status === 'all' || event.status === filters.status));
}

export function registeredFaceUrl(event: MatchEvent, persons: ReferencePerson[]) {
  const face = persons.find(person => person.id === event.person_id)?.faces
    .filter(face => face.state === 'ready' && face.image_available)
    .sort((a, b) => b.quality - a.quality || a.id - b.id)[0];
  return face ? `/api/persons/${event.person_id}/faces/${face.id}/image` : null;
}

/** The recovery cursor advances only after ordered HTTP pages, never from a WS message. */
export class EventFeed {
  private socket: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private refreshTimer: ReturnType<typeof setInterval> | null = null;
  private generation = 0;
  private active = false;
  private syncing = false;
  private restartSnapshot = false;
  private initialized = false;
  private cursor = 0;
  private rows = new Map<number, MatchEvent>();
  private deleted = new Map<number, number>();
  private snapshotWatermark = 0;
  private buffered: EventChange[] = [];
  private retry = 0;
  private visibilityVersion = 0;

  constructor(private read: (path: string) => Promise<EventPage>, private render: (rows: MatchEvent[]) => void,
    private state: (label: string) => void, private expired: () => void,
    private connectSocket: () => WebSocket = () => new WebSocket(
      `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/events`)) {}

  start() {
    this.stop(); this.active = true; this.connect();
    this.refreshTimer = setInterval(() => {void this.sync(true);}, 30000);
  }

  stop() {
    this.active = false; this.generation++;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.refreshTimer) clearInterval(this.refreshTimer);
    this.reconnectTimer = this.refreshTimer = null;
    const socket = this.socket; this.socket = null; socket?.close();
    this.syncing = false; this.initialized = false; this.restartSnapshot = false;
    this.cursor = this.snapshotWatermark = 0; this.rows.clear(); this.deleted.clear(); this.buffered = []; this.render([]);
  }

  private connect() {
    if (!this.active) return;
    const version = ++this.generation;
    this.state('연결 중');
    const socket = this.connectSocket(); this.socket = socket;
    socket.onmessage = message => {
      if (version !== this.generation || !this.active) return;
      let value: any;
      try {value = JSON.parse(message.data);} catch {return;}
      if (value.type === 'ready') {this.retry = 0; void this.sync(!this.initialized);}
      else if (value.type === 'resync') {
        this.visibilityVersion++; this.buffered = []; this.rows.clear(); this.render([]);
        this.restartSnapshot = true; void this.sync(true);
      }
      else if (value.type === 'person_match' || value.type === 'event_deleted') {
        if (this.syncing) {
          if (this.buffered.length >= 128) {this.restartSnapshot = true; socket.close();}
          else this.buffered.push(value);
        } else {this.apply([value]);}
      }
    };
    socket.onclose = message => {
      if (version !== this.generation || !this.active) return;
      if (message.code === 1008) {this.stop(); this.expired(); return;}
      this.generation++; this.syncing = false; this.buffered = [];
      const closedVersion = this.generation;
      // Browsers report a rejected handshake as 1006, hiding the server's 1008.
      void this.read('/api/events?limit=1').catch((error: any) => {
        if (this.active && closedVersion === this.generation && error.status === 401) {
          this.stop(); this.expired();
        }
      });
      this.state('재연결 대기');
      this.reconnectTimer = setTimeout(() => this.connect(), Math.min(10000, 1000 * 2 ** Math.min(this.retry++, 4)));
    };
  }

  update(row: EventChange) {
    this.apply([row]);
    if (this.syncing) {
      if (this.buffered.length < 128) this.buffered.push(row);
      else this.restartSnapshot = true;
    }
  }

  private apply(values: EventChange[], snapshot = false) {
    for (const value of values) {
      const previous = this.rows.get(value.event_id);
      if (value.type === 'event_deleted') {
        if (!previous || previous.change_id <= value.change_id) {
          this.deleted.set(value.event_id, Math.max(this.deleted.get(value.event_id) ?? 0, value.change_id));
          this.rows.delete(value.event_id);
        }
        continue;
      }
      // Deletion is final for this ID. A fresh snapshot lets us discard old tombstones
      // while its high-water mark still fences delayed messages for absent rows.
      if (this.deleted.has(value.event_id)
        || (!snapshot && !previous && value.change_id <= this.snapshotWatermark)) continue;
      if (!previous || previous.change_id <= value.change_id) this.rows.set(value.event_id, value);
    }
    const sorted = [...this.rows.values()].sort((a, b) => b.event_id - a.event_id).slice(0, 100);
    this.rows = new Map(sorted.map(value => [value.event_id, value])); this.render(sorted);
  }

  async sync(snapshot = false) {
    if (!this.active || !this.socket || this.socket.readyState !== 1) return;
    if (this.syncing) {this.restartSnapshot ||= snapshot; return;}
    this.syncing = true;
    const version = this.generation;
    const visibilityVersion = this.visibilityVersion;
    try {
      if (snapshot || this.restartSnapshot) {
        this.restartSnapshot = false;
        this.buffered = [];
        const page = await this.read('/api/events?latest=true&limit=100');
        if (version !== this.generation || visibilityVersion !== this.visibilityVersion || !this.active) return;
        this.rows.clear(); this.apply(page.items, true); this.cursor = page.change_cursor ?? 0;
        this.snapshotWatermark = Math.max(this.snapshotWatermark, this.cursor);
        for (const [id, revision] of this.deleted) {
          if (revision <= this.snapshotWatermark) this.deleted.delete(id);
        }
        this.initialized = true;
      }
      let more = true;
      // Bound each recovery pass; the next pass resumes from the saved cursor.
      for (let pageNumber = 0; more && pageNumber < 20; pageNumber++) {
        const page = await this.read(`/api/events?after_change_id=${this.cursor}&limit=100`);
        if (version !== this.generation || visibilityVersion !== this.visibilityVersion || !this.active) return;
        this.apply(page.items); this.cursor = page.next_cursor; more = page.has_more;
      }
      this.apply(this.buffered); this.buffered = []; this.state('연결됨');
      if (more) {this.state('누락 기록 복구 중'); setTimeout(() => {void this.sync();}, 0);}
    } catch (error: any) {
      if (version !== this.generation || !this.active) return;
      if (error.status === 401) {this.stop(); this.expired();}
      else {this.state('기록 조회 재시도 중'); this.socket?.close();}
    } finally {
      if (version === this.generation) {
        this.syncing = false;
        if (this.restartSnapshot) void this.sync(true);
      }
    }
  }
}

@Component({
  selector: 'app-event-panel', standalone: true, imports: [CommonModule, FormsModule],
  template: `
    <section class="card events-panel">
      <div class="preview-heading"><h2>검색 이벤트</h2><span class="event-connection" [class.connected]="connection() === '연결됨'" role="status">{{ connection() }}</span></div>
      <p class="event-intro">실시간 후보를 비교하고 검토하세요.</p>
      <div class="event-filters">
        <div><label for="event-camera">카메라 범위</label><select id="event-camera" class="form-select form-select-sm" [ngModel]="filters().camera" (ngModelChange)="setFilter('camera', $event)"><option value="selected">선택 카메라</option><option value="all">전체 카메라</option></select></div>
        <div><label for="event-status">이벤트 상태</label><select id="event-status" class="form-select form-select-sm" [ngModel]="filters().status" (ngModelChange)="setFilter('status', $event)"><option value="all">전체 상태</option><option value="candidate">확인 전 후보</option><option value="confirmed">운영자 확인</option><option value="rejected">운영자 거부</option></select></div>
        <div class="event-person-filter"><label for="event-person">인물 이름</label><input id="event-person" type="search" class="form-control form-control-sm" [ngModel]="filters().person" (ngModelChange)="setFilter('person', $event)" maxlength="120" placeholder="이름으로 검색"></div>
      </div>
      <div class="event-filter-summary"><span>최근 {{ events().length }}건 중 {{ visibleEvents().length }}건 표시</span><button type="button" class="btn btn-sm btn-link" (click)="resetFilters()">필터 초기화</button></div>
      @if (deletableEvents().length) {
        <div class="event-list-actions"><button type="button" class="btn btn-sm btn-outline-danger" (click)="deleteVisible()" [disabled]="reviewing() || deleting()">{{ deleting() ? '삭제 중…' : '목록 삭제' }}</button><small>현재 표시된 삭제 가능 이벤트 {{ deletableEvents().length }}건</small></div>
      }
      @if (error()) {<div class="alert alert-warning" role="alert">{{ error() }}</div>}
      <div class="event-grid">
        @for (event of visibleEvents(); track event.event_id) {
          <article class="event-card" [attr.data-event-id]="event.event_id" [class.event-confirmed]="event.status === 'confirmed'" [class.event-rejected]="event.status === 'rejected'">
            <div class="event-details"><div class="event-card-heading"><strong>{{ event.person_name }}</strong><span class="event-status">{{ statusLabel(event.status) }}</span></div>
              <div class="event-photo-pair">
                <figure><figcaption>현재 등록 얼굴</figcaption>
                  @if (referenceUrl(event); as url) {<img class="event-reference-image" [src]="url" [alt]="event.person_name + '의 현재 등록 얼굴'" width="112" height="112" loading="lazy" (error)="imageFailed(url)">} @else {<div class="event-image-empty">등록 사진 없음</div>}
                </figure>
                <figure><figcaption>검출 얼굴</figcaption>
                  @if (detectedUrl(event); as url) {<img class="event-detected-image" [src]="url" [alt]="event.person_name + ' 검색 후보 얼굴'" width="112" height="112" loading="lazy" (error)="imageFailed(url)">} @else {<div class="event-image-empty">사진 만료·삭제</div>}
                </figure>
              </div>
              <small class="event-camera-name">{{ event.camera_name }} · 추적 #{{ event.track_id }}</small>
              <time [attr.datetime]="event.timestamp">{{ event.timestamp | date:'yyyy-MM-dd HH:mm:ss' }}</time>
              <div class="event-scores"><span>유사도 <strong>{{ event.face_similarity | number:'1.3-3' }}</strong></span><span>얼굴 품질 <strong>{{ event.face_quality | number:'1.2-2' }}</strong></span></div>
              @if (event.clip_state === 'pending') {<small>영상 클립 준비 중</small>}
              @if (event.clip_state === 'failed') {<small>영상 클립 저장 실패 · {{ event.clip_error }}</small>}
              @if (event.clip_state === 'expired') {<small>영상 클립 보관 기간 만료</small>}
              @if (event.video_clip_url) {
                <details class="event-clip"><summary>영상 클립{{ event.clip_details?.partial ? ' (일부 구간)' : '' }}</summary>
                  <video controls preload="none" [src]="event.video_clip_url" style="width:100%" aria-label="검색 이벤트 영상 클립"></video>
                  <small>검출 전 {{ event.clip_details?.before_seconds | number:'1.1-1' }}초 · 후 {{ event.clip_details?.after_seconds | number:'1.1-1' }}초 · 음성 없음</small>
                </details>
              }
              <div class="event-actions">
                @if (event.frame_url) {<a [href]="event.frame_url" target="_blank" rel="noopener" class="btn btn-sm btn-outline-secondary">검출 프레임</a>}
                @if (event.can_review) {
                  <button class="btn btn-sm btn-outline-primary" (click)="review(event, 'confirm')" [disabled]="reviewing() || deleting() || event.status === 'confirmed'">확인</button>
                  <button class="btn btn-sm btn-outline-danger" (click)="review(event, 'reject')" [disabled]="reviewing() || deleting() || event.status === 'rejected'">거부</button>
                }
                @if (event.can_delete) {<button type="button" class="btn btn-sm btn-outline-danger" (click)="deleteEvent(event)" [disabled]="reviewing() || deleting()">삭제</button>}
              </div>
            </div>
          </article>
        } @empty {<div class="event-empty">{{ events().length ? '조건에 맞는 이벤트가 없습니다. 필터를 변경해 주세요.' : '저장된 검색 후보가 없습니다. 분석 중 품질과 유사도 기준을 통과하면 표시됩니다.' }}</div>}
      </div>
      <small class="face-note">권한이 있는 최근 100건 안에서 필터링합니다. 등록 얼굴은 현재 사진이며 검출 당시의 등록 사진과 다를 수 있습니다. 검색 후보는 동일인 확정이 아닙니다.</small>
    </section>`
})
export class EventPanelComponent implements OnInit, OnDestroy {
  @Input() requestHeaders: Record<string, string> = {};
  @Input() set selectedCameraId(value: number | null) {this.cameraId.set(value);}
  @Input() set references(value: ReferencePerson[]) {this.persons.set(value);}
  @Output() sessionExpired = new EventEmitter<void>();
  private http = inject(HttpClient);
  private destroyRef = inject(DestroyRef);
  events = signal<MatchEvent[]>([]);
  private cameraId = signal<number | null>(null);
  private persons = signal<ReferencePerson[]>([]);
  private failedImages = signal(new Set<string>());
  filters = signal<EventFilters>({camera: 'selected', person: '', status: 'all'});
  visibleEvents = computed(() => filterEvents(this.events(), this.filters(), this.cameraId()));
  deletableEvents = computed(() => this.visibleEvents().filter(event => event.can_delete));
  connection = signal('연결 중');
  error = signal('');
  reviewing = signal(false);
  deleting = signal(false);
  private feed = new EventFeed(path => firstValueFrom(this.http.get<EventPage>(path).pipe(timeout(8000))),
    rows => this.events.set(rows), label => this.connection.set(label), () => this.sessionExpired.emit());
  ngOnInit() {this.feed.start();}
  ngOnDestroy() {this.feed.stop();}
  setFilter(key: keyof EventFilters, value: string) {this.filters.update(filters => ({...filters, [key]: value}));}
  resetFilters() {this.filters.set({camera: 'selected', person: '', status: 'all'});}
  imageFailed(url: string) {this.failedImages.update(values => new Set([...values, url].slice(-256)));}
  referenceUrl(event: MatchEvent) {
    const url = registeredFaceUrl(event, this.persons());
    return url && !this.failedImages().has(url) ? url : null;
  }
  detectedUrl(event: MatchEvent) {
    const url = event.thumbnail_url ? `${event.thumbnail_url}?v=${event.change_id}` : null;
    return url && !this.failedImages().has(url) ? url : null;
  }
  statusLabel(status: string) {return ({candidate:'확인 전 후보', confirmed:'운영자 확인', rejected:'운영자 거부'} as Record<string, string>)[status] || status;}
  deleteEvent(event: MatchEvent) {
    if (this.reviewing() || this.deleting() || !event.can_delete
      || !window.confirm(`“${event.person_name}” 검색 이벤트와 검출 사진·영상 클립을 삭제할까요? 삭제 후 복구할 수 없습니다.`)) return;
    this.deleting.set(true); this.error.set('');
    this.http.delete<EventDeletion>(`/api/events/${event.event_id}`, {headers:this.requestHeaders})
      .pipe(timeout(8000), takeUntilDestroyed(this.destroyRef)).subscribe({
        next: value => {this.feed.update(value); this.deleting.set(false);},
        error: error => this.deleteFailed(error)
      });
  }
  deleteVisible() {
    const rows = this.deletableEvents();
    if (this.reviewing() || this.deleting() || !rows.length
      || !window.confirm(`현재 필터에 표시된 삭제 가능 이벤트 ${rows.length}건과 검출 사진을 삭제할까요? 삭제 후 복구할 수 없습니다.`)) return;
    this.deleting.set(true); this.error.set('');
    this.http.post<{items: EventDeletion[]}>('/api/events/delete', {event_ids:rows.map(event => event.event_id)}, {headers:this.requestHeaders})
      .pipe(timeout(8000), takeUntilDestroyed(this.destroyRef)).subscribe({
        next: value => {for (const row of value.items) this.feed.update(row); this.deleting.set(false);},
        error: error => this.deleteFailed(error)
      });
  }
  private deleteFailed(error: {status?: number}) {
    this.deleting.set(false);
    if (error.status === 401) {this.feed.stop(); this.sessionExpired.emit();}
    else {this.error.set('이벤트를 삭제하지 못했습니다. 접근 권한과 서버 연결을 확인해 주세요.'); void this.feed.sync(true);}
  }
  review(event: MatchEvent, action: 'confirm' | 'reject') {
    if (this.reviewing() || this.deleting()) return;
    this.reviewing.set(true); this.error.set('');
    this.http.post<MatchEvent>(`/api/events/${event.event_id}/${action}`, {}, {headers:this.requestHeaders})
      .pipe(timeout(8000), takeUntilDestroyed(this.destroyRef)).subscribe({
        next: value => {this.feed.update(value); this.reviewing.set(false);},
        error: error => {
          this.reviewing.set(false);
          if (error.status === 401) {this.feed.stop(); this.sessionExpired.emit();}
          else {this.error.set('이벤트를 저장하지 못했습니다. 접근 권한과 서버 연결을 확인해 주세요.'); void this.feed.sync(true);}
        }
      });
  }
}
