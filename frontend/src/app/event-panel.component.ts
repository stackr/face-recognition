import {CommonModule} from '@angular/common';
import {HttpClient} from '@angular/common/http';
import {Component, DestroyRef, ElementRef, EventEmitter, Input, OnDestroy, OnInit, Output, ViewChild, computed, inject, signal} from '@angular/core';
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
    this.state('Connecting');
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
      this.state('Waiting to reconnect');
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
      this.apply(this.buffered); this.buffered = []; this.state('Connected');
      if (more) {this.state('Recovering missing records'); setTimeout(() => {void this.sync();}, 0);}
    } catch (error: any) {
      if (version !== this.generation || !this.active) return;
      if (error.status === 401) {this.stop(); this.expired();}
      else {this.state('Retrying record retrieval'); this.socket?.close();}
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
      <div class="preview-heading"><h2>Search Events</h2><span class="event-connection" [class.connected]="connection() === 'Connected'" role="status">{{ connection() }}</span></div>
      <p class="event-intro">Compare and review live candidates.</p>
      <div class="event-filters">
        <div><label for="event-camera">Camera Scope</label><select id="event-camera" class="form-select form-select-sm" [ngModel]="filters().camera" (ngModelChange)="setFilter('camera', $event)"><option value="selected">Selected camera</option><option value="all">All cameras</option></select></div>
        <div><label for="event-status">Event Status</label><select id="event-status" class="form-select form-select-sm" [ngModel]="filters().status" (ngModelChange)="setFilter('status', $event)"><option value="all">All statuses</option><option value="candidate">Unconfirmed candidate</option><option value="confirmed">Confirmed by operator</option><option value="rejected">Rejected by operator</option></select></div>
        <div class="event-person-filter"><label for="event-person">Person Name</label><input id="event-person" type="search" class="form-control form-control-sm" [ngModel]="filters().person" (ngModelChange)="setFilter('person', $event)" maxlength="120" placeholder="Search by name"></div>
      </div>
      <div class="event-filter-summary"><span>Showing {{ visibleEvents().length }} of the latest {{ events().length }} events</span><button type="button" class="btn btn-sm btn-link" (click)="resetFilters()">Reset Filters</button></div>
      @if (deletableEvents().length) {
        <div class="event-list-actions"><button type="button" class="btn btn-sm btn-outline-danger" (click)="deleteVisible()" [disabled]="reviewing() || deleting()">{{ deleting() ? 'Deleting…' : 'Delete List' }}</button><small>{{ deletableEvents().length }} displayed events can be deleted</small></div>
      }
      @if (error()) {<div class="alert alert-warning" role="alert">{{ error() }}</div>}
      <div class="event-grid">
        @for (event of visibleEvents(); track event.event_id) {
          <article class="event-card" [attr.data-event-id]="event.event_id" [class.event-confirmed]="event.status === 'confirmed'" [class.event-rejected]="event.status === 'rejected'">
            <div class="event-details"><div class="event-card-heading"><strong>{{ event.person_name }}</strong><span class="event-status">{{ statusLabel(event.status) }}</span></div>
              <div class="event-photo-pair">
                <figure><figcaption>Current Reference Face</figcaption>
                  @if (referenceUrl(event); as url) {<img class="event-reference-image" [src]="url" [alt]="event.person_name + ' current reference face'" width="112" height="112" loading="lazy" (error)="imageFailed(url)">} @else {<div class="event-image-empty">No reference image</div>}
                </figure>
                <figure><figcaption>Detected Face</figcaption>
                  @if (detectedUrl(event); as url) {<img class="event-detected-image" [src]="url" [alt]="event.person_name + ' Search candidate face'" width="112" height="112" loading="lazy" (error)="imageFailed(url)">} @else {<div class="event-image-empty">Image expired or deleted</div>}
                </figure>
              </div>
              <small class="event-camera-name">{{ event.camera_name }} · Track #{{ event.track_id }}</small>
              <time [attr.datetime]="event.timestamp">{{ event.timestamp | date:'yyyy-MM-dd HH:mm:ss' }}</time>
              <div class="event-scores"><span>Similarity <strong>{{ event.face_similarity | number:'1.3-3' }}</strong></span><span>Face Quality <strong>{{ event.face_quality | number:'1.2-2' }}</strong></span></div>
              @if (event.clip_state === 'pending') {<small>Preparing video clip</small>}
              @if (event.clip_state === 'failed') {<small>Video clip storage failed · {{ event.clip_error }}</small>}
              @if (event.clip_state === 'expired') {<small>Video clip retention expired</small>}
              @if (event.video_clip_url) {
                <details class="event-clip"><summary>Video Clip{{ event.clip_details?.partial ? ' (Partial segment)' : '' }}</summary>
                  <video controls preload="none" [src]="event.video_clip_url" style="width:100%" aria-label="Search Event Video Clip"></video>
                  <small>Before Detection {{ event.clip_details?.before_seconds | number:'1.1-1' }} seconds · After {{ event.clip_details?.after_seconds | number:'1.1-1' }} seconds · No audio</small>
                </details>
              }
              <div class="event-actions">
                @if (event.frame_url) {<button type="button" (click)="openFrame(event)" class="btn btn-sm btn-outline-secondary">Detected Frame</button>}
                @if (event.can_review) {
                  <button class="btn btn-sm btn-outline-primary" (click)="review(event, 'confirm')" [disabled]="reviewing() || deleting() || event.status === 'confirmed'">Confirm</button>
                  <button class="btn btn-sm btn-outline-danger" (click)="review(event, 'reject')" [disabled]="reviewing() || deleting() || event.status === 'rejected'">Reject</button>
                }
                @if (event.can_delete) {<button type="button" class="btn btn-sm btn-outline-danger" (click)="deleteEvent(event)" [disabled]="reviewing() || deleting()">Delete</button>}
              </div>
            </div>
          </article>
        } @empty {<div class="event-empty">{{ events().length ? 'No matching events. Change the filters.' : 'No saved search candidates. Candidates appear when quality and similarity thresholds are met during analysis.' }}</div>}
      </div>
      <small class="face-note">Filters the latest 100 events you can access. Reference images are current and may differ from those available when the event was detected. A search candidate does not confirm identity.</small>
    </section>
    <dialog #frameDialog class="event-frame-dialog" aria-labelledby="event-frame-title" (click)="frameBackdrop($event)" (cancel)="closeFrame()" (close)="frameClosed()">
      <div class="event-frame-content">
        <div class="event-frame-heading"><h2 id="event-frame-title">Detected Frame</h2><button type="button" class="btn btn-outline-secondary" autofocus (click)="closeFrame()" aria-label="Close Detected Frame">Close</button></div>
        @if (frameEvent(); as event) {
          <p class="event-frame-caption">{{ event.person_name }} · {{ event.camera_name }} · Track #{{ event.track_id }} · {{ event.timestamp | date:'yyyy-MM-dd HH:mm:ss' }}</p>
          @if (frameLoading()) {<p class="event-frame-message" role="status">Loading the detected frame.</p>}
          @if (frameError()) {<p class="alert alert-warning" role="alert">Could not load the detected frame. The image may have expired or access permissions may have changed.</p>}
          <img class="event-frame-image" [class.d-none]="frameLoading() || frameError()" [src]="event.frame_url" [alt]="event.person_name + ' Search Event Detected Frame'" (load)="frameLoading.set(false)" (error)="frameError.set(true); frameLoading.set(false)">
        }
      </div>
    </dialog>`
})
export class EventPanelComponent implements OnInit, OnDestroy {
  @Input() requestHeaders: Record<string, string> = {};
  @Input() set selectedCameraId(value: number | null) {if (this.cameraId() !== value) this.closeFrame(); this.cameraId.set(value);}
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
  connection = signal('Connecting');
  error = signal('');
  reviewing = signal(false);
  deleting = signal(false);
  frameEvent = signal<MatchEvent | null>(null);
  frameLoading = signal(false);
  frameError = signal(false);
  @ViewChild('frameDialog', {static:true}) private frameDialog!: ElementRef<HTMLDialogElement>;
  private feed = new EventFeed(path => firstValueFrom(this.http.get<EventPage>(path).pipe(timeout(8000))),
    rows => {
      this.events.set(rows);
      const frame = this.frameEvent();
      if (frame && !rows.some(row => row.event_id === frame.event_id && row.frame_url)) this.closeFrame();
    }, label => this.connection.set(label), () => this.sessionExpired.emit());
  ngOnInit() {this.feed.start();}
  ngOnDestroy() {this.closeFrame(); this.feed.stop();}
  openFrame(event: MatchEvent) {
    if (!event.frame_url) return;
    this.frameLoading.set(true); this.frameError.set(false); this.frameEvent.set(event);
    this.frameDialog.nativeElement.showModal();
  }
  closeFrame() {this.frameDialog?.nativeElement.close(); this.frameClosed();}
  frameClosed() {
    if (this.frameDialog?.nativeElement.open) return;
    this.frameEvent.set(null); this.frameLoading.set(false); this.frameError.set(false);
  }
  frameBackdrop(event: MouseEvent) {if (event.target === this.frameDialog.nativeElement) this.closeFrame();}
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
  statusLabel(status: string) {return ({candidate:'Unconfirmed candidate', confirmed:'Confirmed by operator', rejected:'Rejected by operator'} as Record<string, string>)[status] || status;}
  deleteEvent(event: MatchEvent) {
    if (this.reviewing() || this.deleting() || !event.can_delete
      || !window.confirm(`“${event.person_name}” Delete the search event, detected image and video clip? This cannot be undone.`)) return;
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
      || !window.confirm(`Delete ${rows.length} events matching the current filters and their detected images? This cannot be undone.`)) return;
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
    else {this.error.set('Could not delete the event. Check your access permissions and server connection.'); void this.feed.sync(true);}
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
          else {this.error.set('Could not save the event. Check your access permissions and server connection.'); void this.feed.sync(true);}
        }
      });
  }
}
