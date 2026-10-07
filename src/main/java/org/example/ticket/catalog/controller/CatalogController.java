package org.example.ticket.catalog.controller;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.example.ticket.common.response.ApiResponse;
import org.example.ticket.performance.dto.*;
import org.example.ticket.performance.service.PerformanceService;
import org.example.ticket.venue.dto.CreateVenueHallRequest;
import org.example.ticket.venue.service.VenueService;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/catalog")
@RequiredArgsConstructor
public class CatalogController {
    private final VenueService venueService;
    private final PerformanceService performanceService;

    @PostMapping("/halls")
    public ApiResponse<Long> createHall(@Valid @RequestBody CreateVenueHallRequest request) {
        return ApiResponse.success(venueService.createHall(request));
    }

    @PostMapping("/performances")
    public ApiResponse<Long> createPerformance(@Valid @RequestBody CreatePerformanceRequest request) {
        return ApiResponse.success(performanceService.createPerformance(request));
    }

    @PostMapping("/performances/{performanceId}/times")
    public ApiResponse<Long> createPerformanceTime(@PathVariable Long performanceId,
                                                    @Valid @RequestBody CreatePerformanceTimeRequest request) {
        return ApiResponse.success(performanceService.createPerformanceTime(performanceId, request));
    }
}
