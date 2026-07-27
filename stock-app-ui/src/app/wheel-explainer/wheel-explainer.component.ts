import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'app-wheel-explainer',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './wheel-explainer.component.html',
  styleUrls: ['./wheel-explainer.component.css']
})
export class WheelExplainerComponent {
  /** Anchor list for the on-this-page rail. */
  readonly sections = [
    { id: "sec-read-this-first-analytics-tooling-not-tr", label: "⚠ Read this first — analytics tooling, not trade selection" },
    { id: "sec-1-what-good-for-wheeling-decomposes-into", label: "1. What \"good for wheeling\" decomposes into" },
    { id: "sec-2-terminal-expiry-itm-vs-touch-the-key-d", label: "2. Terminal (expiry-ITM) vs Touch — the key distinction" },
    { id: "sec-3-sample-count-guidance", label: "3. Sample-count guidance" },
    { id: "sec-4-volatility-expected-move-conventions", label: "4. Volatility & expected-move conventions" },
    { id: "sec-5-earnings-freshness-the-tri-state", label: "5. Earnings freshness — the tri-state" },
    { id: "sec-6-null-rendering", label: "6. Null rendering" },
    { id: "sec-7-column-dictionary", label: "7. Column dictionary" },
    { id: "sec-8-what-the-wheel-scan-does-not-do", label: "8. What the Wheel Scan does NOT do" },
    { id: "sec-9-wheel-scan-vs-option-quotes", label: "9. Wheel Scan vs Option Quotes" }
  ];

  jumpTo(event: Event, id: string): void {
    event.preventDefault();
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  jumpToValue(id: string): void {
    if (id) {
      document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

}
