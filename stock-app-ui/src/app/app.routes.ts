import { ActivatedRouteSnapshot, Routes } from '@angular/router';
import { PageNotFoundComponent } from './page-not-found/page-not-found.component';

/** Stock detail is the one route whose title depends on the URL. */
const stockDetailTitle = (route: ActivatedRouteSnapshot) => {
  const symbol = route.paramMap.get('symbol');
  return symbol ? `${symbol.toUpperCase()} · smallFish` : 'Stock Detail · smallFish';
};

export const routes: Routes = [
  { path: 'momentum', title: 'Momentum · smallFish', loadComponent: () => import('./momentum-scanner/momentum-scanner.component').then(m => m.MomentumScannerComponent) },
  { path: 'studies', title: 'Research Studies · smallFish', loadComponent: () => import('./studies/studies.component').then(m => m.StudiesComponent) },
  { path: 'studies/:studyId', title: 'Research Studies · smallFish', loadComponent: () => import('./studies/studies.component').then(m => m.StudiesComponent) },
  { path: 'sectors', title: 'Sectors · smallFish', loadComponent: () => import('./sector-rotation/sector-rotation.component').then(m => m.SectorRotationComponent) },
  { path: 'wheel', title: 'Wheel · smallFish', loadComponent: () => import('./wheel/wheel.component').then(m => m.WheelComponent) },
  { path: 'wheelExplainer', title: 'Wheel Explainer · smallFish', loadComponent: () => import('./wheel-explainer/wheel-explainer.component').then(m => m.WheelExplainerComponent) },
  { path: 'options', title: 'Trading Ledger · smallFish', loadComponent: () => import('./options/options.component').then(m => m.OptionsComponent) },
  { path: 'retirement', title: 'Retirement Ledger · smallFish', loadComponent: () => import('./retirement-portfolio/retirement-portfolio.component').then(m => m.RetirementPortfolioComponent) },
  { path: 'portfolios', title: 'Portfolios · smallFish', loadComponent: () => import('./portfolios/portfolios.component').then(m => m.PortfoliosComponent) },
  { path: 'stockDetail/:symbol', title: stockDetailTitle, loadComponent: () => import('./stock-detail/stock-detail.component').then(m => m.StockDetailComponent) },
  { path: '', redirectTo: '/momentum', pathMatch: 'full' },
  { path: '**', component: PageNotFoundComponent, title: 'Not Found · smallFish' }
];
