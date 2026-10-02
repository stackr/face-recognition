import { provideHttpClient } from '@angular/common/http';
import { bootstrapApplication } from '@angular/platform-browser';
import { AppComponent } from './app/app.component';

bootstrapApplication(AppComponent, { providers: [provideHttpClient()] })
  .catch(() => { document.body.textContent = '화면을 시작하지 못했습니다. 새로고침해 주세요.'; });

