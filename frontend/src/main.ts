import { provideHttpClient } from '@angular/common/http';
import { bootstrapApplication } from '@angular/platform-browser';
import { AppComponent } from './app/app.component';

bootstrapApplication(AppComponent, { providers: [provideHttpClient()] })
  .catch(() => { document.body.textContent = 'Could not start the page. Please refresh.'; });

