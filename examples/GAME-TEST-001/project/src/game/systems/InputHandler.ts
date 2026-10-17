// src/game/systems/InputHandler.ts
// Feature f02-navigation: Unified mouse and touch input handler for lateral squad control.

/**
 * InputHandler captures horizontal drag input from mouse or touch events
 * on a given HTML element. It converts screen-space horizontal movement
 * into world-space X coordinates for the NavigationSystem.
 *
 * - Press/touch down starts tracking.
 * - Dragging updates the target X position (delta-based).
 * - Release clears the target (returns null), stopping horizontal movement.
 * - Supports both portrait and landscape orientations.
 */
export class InputHandler {
  private el: HTMLElement | null = null;
  private targetX: number | null = null;
  private currentX = 0;
  private isDragging = false;
  private lastScreenX = 0;
  private canvasWidth = 1;
  private roadHalfWidth = 5.5; // default, updated via config if needed

  // Bound handlers for proper removal
  private handleMouseDown: ((e: MouseEvent) => void) | null = null;
  private handleMouseMove: ((e: MouseEvent) => void) | null = null;
  private handleMouseUp: ((e: MouseEvent) => void) | null = null;
  private handleTouchStart: ((e: TouchEvent) => void) | null = null;
  private handleTouchMove: ((e: TouchEvent) => void) | null = null;
  private handleTouchEnd: ((e: TouchEvent) => void) | null = null;
  private handleResize: (() => void) | null = null;
  private handleContextMenu: ((e: Event) => void) | null = null;
  private activePointer: number | null = null;
  private handlePointerDown: ((e: PointerEvent) => void) | null = null;
  private handlePointerMove: ((e: PointerEvent) => void) | null = null;
  private handlePointerUp: ((e: PointerEvent) => void) | null = null;

  /**
   * Bind all input listeners to the given element (typically the game canvas container).
   * @param el The HTML element to listen on.
   */
  bind(el: HTMLElement): void {
    this.unbind();
    this.el = el;
    this.updateCanvasWidth();

    // Prevent context menu on long press (mobile)
    this.handleContextMenu = (e: Event) => {
      e.preventDefault();
    };
    el.addEventListener('contextmenu', this.handleContextMenu);

    // --- Mouse events ---
    this.handleMouseDown = (e: MouseEvent) => {
      this.isDragging = true;
      this.lastScreenX = e.clientX;
      if (this.targetX === null) {
        this.targetX = this.currentX;
      }
    };

    this.handleMouseMove = (e: MouseEvent) => {
      if (!this.isDragging) return;
      const dx = e.clientX - this.lastScreenX;
      this.lastScreenX = e.clientX;
      this.applyDelta(dx);
    };

    this.handleMouseUp = (_e: MouseEvent) => {
      this.isDragging = false;
      this.targetX = null;
    };

    // --- Touch events ---
    this.handleTouchStart = (e: TouchEvent) => {
      if (e.touches.length > 0) {
        this.isDragging = true;
        this.lastScreenX = e.touches[0].clientX;
        if (this.targetX === null) {
          this.targetX = this.currentX;
        }
        // Prevent default to avoid scroll interference
        e.preventDefault();
      }
    };

    this.handleTouchMove = (e: TouchEvent) => {
      if (!this.isDragging || e.touches.length === 0) return;
      const dx = e.touches[0].clientX - this.lastScreenX;
      this.lastScreenX = e.touches[0].clientX;
      this.applyDelta(dx);
      e.preventDefault();
    };

    this.handleTouchEnd = (_e: TouchEvent) => {
      this.isDragging = false;
      this.targetX = null;
    };

    // --- Resize observer for orientation changes ---
    this.handleResize = () => {
      this.updateCanvasWidth();
    };

    // Babylon consumes pointerdown and can suppress compatibility mousedown.
    // Capture the actual pointer before the canvas handler; never double-apply
    // its later compatibility mouse/touch events.
    if (typeof PointerEvent !== 'undefined') {
      this.handlePointerDown = e => {
        if(this.activePointer!==null||!e.isPrimary||e.button!==0)return;
        this.activePointer=e.pointerId;this.isDragging=true;this.lastScreenX=e.clientX;
        this.targetX=this.currentX;el.setPointerCapture?.(e.pointerId);e.preventDefault();
      };
      this.handlePointerMove = e => {
        if(!this.isDragging||e.pointerId!==this.activePointer)return;
        const dx=e.clientX-this.lastScreenX;this.lastScreenX=e.clientX;this.applyDelta(dx);e.preventDefault();
      };
      this.handlePointerUp = e => {
        if(e.pointerId!==this.activePointer)return;
        this.activePointer=null;this.isDragging=false;this.targetX=null;
        if(el.hasPointerCapture?.(e.pointerId))el.releasePointerCapture(e.pointerId);
      };
      el.addEventListener('pointerdown',this.handlePointerDown,{capture:true,passive:false});
      window.addEventListener('pointermove',this.handlePointerMove,{capture:true,passive:false});
      window.addEventListener('pointerup',this.handlePointerUp,true);
      window.addEventListener('pointercancel',this.handlePointerUp,true);
    } else {
      el.addEventListener('mousedown', this.handleMouseDown);
      window.addEventListener('mousemove', this.handleMouseMove);
      window.addEventListener('mouseup', this.handleMouseUp);
      el.addEventListener('touchstart', this.handleTouchStart, { passive: false });
      el.addEventListener('touchmove', this.handleTouchMove, { passive: false });
      el.addEventListener('touchend', this.handleTouchEnd);
      el.addEventListener('touchcancel', this.handleTouchEnd);
    }

    window.addEventListener('resize', this.handleResize);
  }

  /**
   * Remove all event listeners and reset state.
   */
  unbind(): void {
    if (this.el) {
      if(this.activePointer!==null&&this.el.hasPointerCapture?.(this.activePointer))this.el.releasePointerCapture(this.activePointer);
      if(this.handlePointerDown)this.el.removeEventListener('pointerdown',this.handlePointerDown,true);
      if (this.handleContextMenu) {
        this.el.removeEventListener('contextmenu', this.handleContextMenu);
      }
      if (this.handleMouseDown) {
        this.el.removeEventListener('mousedown', this.handleMouseDown);
      }
      if (this.handleTouchStart) {
        this.el.removeEventListener('touchstart', this.handleTouchStart);
      }
      if (this.handleTouchMove) {
        this.el.removeEventListener('touchmove', this.handleTouchMove);
      }
      if (this.handleTouchEnd) {
        this.el.removeEventListener('touchend', this.handleTouchEnd);
        this.el.removeEventListener('touchcancel', this.handleTouchEnd);
      }
    }

    if (this.handleMouseMove) {
      window.removeEventListener('mousemove', this.handleMouseMove);
    }
    if (this.handleMouseUp) {
      window.removeEventListener('mouseup', this.handleMouseUp);
    }
    if (this.handleResize) {
      window.removeEventListener('resize', this.handleResize);
    }
    if(this.handlePointerMove)window.removeEventListener('pointermove',this.handlePointerMove,true);
    if(this.handlePointerUp){window.removeEventListener('pointerup',this.handlePointerUp,true);window.removeEventListener('pointercancel',this.handlePointerUp,true);}

    this.el = null;
    this.isDragging = false;
    this.targetX = null;
    this.handleMouseDown = null;
    this.handleMouseMove = null;
    this.handleMouseUp = null;
    this.handleTouchStart = null;
    this.handleTouchMove = null;
    this.handleTouchEnd = null;
    this.handleResize = null;
    this.handleContextMenu = null;
    this.activePointer=null;this.handlePointerDown=null;this.handlePointerMove=null;this.handlePointerUp=null;
  }

  /**
   * Get the current target X position in world space.
   * Returns null when no input is active (player released / not dragging).
   */
  setCurrentX(x: number): void { this.currentX=x; }

  getTargetX(): number | null {
    return this.targetX;
  }

  /**
   * Set the road half-width for clamping. Should be called when config is available.
   * Default is 5.5 per spec (road boundaries -5.5 to 5.5).
   */
  setRoadHalfWidth(halfWidth: number): void {
    this.roadHalfWidth = halfWidth;
    if (this.targetX !== null) {
      this.targetX = Math.max(-this.roadHalfWidth, Math.min(this.roadHalfWidth, this.targetX));
    }
  }

  /**
   * Set an absolute target X directly (e.g., from a dispatch command).
   */
  setAbsoluteTargetX(x: number): void {
    this.targetX = Math.max(-this.roadHalfWidth, Math.min(this.roadHalfWidth, x));
  }

  private updateCanvasWidth(): void {
    if (this.el) {
      const rect = this.el.getBoundingClientRect();
      this.canvasWidth = Math.max(rect.width, 1);
    }
  }

  /**
   * Convert a screen-space pixel delta to world-space X delta and apply it.
   * Mapping: full canvas width maps to full road width (2 * roadHalfWidth).
   */
  private applyDelta(screenDx: number): void {
    if (this.targetX === null) {
      this.targetX = this.currentX;
    }

    // Convert pixel delta to world units
    // The entire canvas width represents the full road width
    const worldDx = (screenDx / this.canvasWidth) * (this.roadHalfWidth * 2);
    this.targetX += worldDx;

    // Clamp to road boundaries
    this.targetX = Math.max(
      -this.roadHalfWidth,
      Math.min(this.roadHalfWidth, this.targetX)
    );
  }
}
