import {CommonModule} from '@angular/common';
import {HttpClient} from '@angular/common/http';
import {Component, EventEmitter, Input, OnDestroy, OnInit, Output, inject, signal} from '@angular/core';
import {firstValueFrom, timeout} from 'rxjs';

export interface MatchEvent {
  type: 'person_match'; event_id: number; change_id: number; camera_id: number; camera_name: string;
  person_id: number; person_name: string; stream_session_id: string; track_id: number;
  timestamp: string; face_similarity: number; face_quality: number;
  status: 'candidate' | 'confirmed' | 'rejected'; thumbnail_url: string | null; frame_url: string | null;
  can_review: boolean;
}
export interface EventPage {items: MatchEvent[]; next_cursor: number; change_cursor?: number; has_more: boolean;}

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
  private buffered: MatchEvent[] = [];
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
    this.cursor = 0; this.rows.clear(); this.buffered = []; this.render([]);
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
      else if (value.type === 'person_match') {
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

  update(row: MatchEvent) {this.apply([row]);}

  private apply(values: MatchEvent[]) {
    for (const value of values) {
      const previous = this.rows.get(value.event_id);
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
        this.rows.clear(); this.apply(page.items); this.cursor = page.change_cursor ?? 0;
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
  selector: 'app-event-panel', standalone: true, imports: [CommonModule],
  template: `
    <section class="card events-panel">
      <div class="preview-heading"><h2>검색 이벤트</h2><span class="text-secondary" role="status">{{ connection() }}</span></div>
      <p>권한이 있는 카메라·인물의 최근 100건입니다. 후보를 확인하거나 거부할 수 있습니다.</p>
      @if (error()) {<div class="alert alert-warning" role="alert">{{ error() }}</div>}
      <div class="event-grid">
        @for (event of events(); track event.event_id) {
          <article class="event-card">
            @if (event.thumbnail_url) {<img [src]="event.thumbnail_url + '?v=' + event.change_id" [alt]="event.person_name + ' 검색 후보 얼굴'" width="112" height="112">}
            <div class="event-details"><strong>{{ event.person_name }}</strong><small>{{ event.camera_name }} · 추적 #{{ event.track_id }}</small>
              <small>{{ event.timestamp | date:'yyyy-MM-dd HH:mm:ss' }}</small>
              <small>유사도 {{ event.face_similarity | number:'1.3-3' }} · 얼굴 품질 {{ event.face_quality | number:'1.2-2' }}</small>
              <span class="event-status">{{ statusLabel(event.status) }}</span>
              <div class="event-actions">
                @if (event.frame_url) {<a [href]="event.frame_url" target="_blank" rel="noopener" class="btn btn-sm btn-outline-secondary">검출 프레임</a>}
                @if (event.can_review) {
                  <button class="btn btn-sm btn-outline-primary" (click)="review(event, 'confirm')" [disabled]="reviewing() || event.status === 'confirmed'">확인</button>
                  <button class="btn btn-sm btn-outline-danger" (click)="review(event, 'reject')" [disabled]="reviewing() || event.status === 'rejected'">거부</button>
                }
              </div>
            </div>
          </article>
        } @empty {<div class="text-secondary">저장된 검색 후보가 없습니다. 분석 중 품질과 유사도 기준을 통과하면 표시됩니다.</div>}
      </div>
      <small class="face-note">검색 후보는 동일인 확정이 아닙니다. 이벤트 사진은 기본 7일, 기록은 30일 보관합니다.</small>
    </section>`
})
export class EventPanelComponent implements OnInit, OnDestroy {
  @Input() requestHeaders: Record<string, string> = {};
  @Output() sessionExpired = new EventEmitter<void>();
  private http = inject(HttpClient);
  events = signal<MatchEvent[]>([]);
  connection = signal('연결 중');
  error = signal('');
  reviewing = signal(false);
  private feed = new EventFeed(path => firstValueFrom(this.http.get<EventPage>(path).pipe(timeout(8000))),
    rows => this.events.set(rows), label => this.connection.set(label), () => this.sessionExpired.emit());
  ngOnInit() {this.feed.start();}
  ngOnDestroy() {this.feed.stop();}
  statusLabel(status: string) {return ({candidate:'확인 전 후보', confirmed:'운영자 확인', rejected:'운영자 거부'} as Record<string, string>)[status] || status;}
  review(event: MatchEvent, action: 'confirm' | 'reject') {
    if (this.reviewing()) return;
    this.reviewing.set(true); this.error.set('');
    this.http.post<MatchEvent>(`/api/events/${event.event_id}/${action}`, {}, {headers:this.requestHeaders})
      .pipe(timeout(8000)).subscribe({
        next: value => {this.feed.update(value); this.reviewing.set(false);},
        error: error => {
          this.reviewing.set(false);
          if (error.status === 401) {this.feed.stop(); this.sessionExpired.emit();}
          else {this.error.set('이벤트를 저장하지 못했습니다. 접근 권한과 서버 연결을 확인해 주세요.'); void this.feed.sync(true);}
        }
      });
  }
}
