import { Component, inject, ChangeDetectionStrategy } from '@angular/core';
import { Router, RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  changeDetection: ChangeDetectionStrategy.Eager,
  styleUrl: './app.css'
})
export class App {
  private readonly router = inject(Router);

  /** Keep Wheel marked as current while its in-context explainer is open. */
  isWheelExplainer(): boolean {
    return this.router.url.startsWith('/wheelExplainer');
  }
}
