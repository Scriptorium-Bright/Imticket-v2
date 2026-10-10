package org.example.ticket.seat.controller;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.example.ticket.common.response.ApiResponse;
import org.example.ticket.seat.dto.SeatResponse;
import org.example.ticket.seat.dto.SeatSelectionRequest;
import org.example.ticket.seat.dto.SeatSelectionSummaryResponse;
import org.example.ticket.seat.repository.SeatRepository;
import org.example.ticket.seat.service.SeatSelectionService;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/seats")
@RequiredArgsConstructor
public class SeatController {
    private final SeatRepository seatRepository;
    private final SeatSelectionService seatSelectionService;

    @GetMapping("/{performanceTimeId}")
    public ApiResponse<List<SeatResponse>> seatMap(@PathVariable Long performanceTimeId) {
        return ApiResponse.success(seatRepository.findSeatMap(performanceTimeId));
    }

    @PostMapping("/selection-summary")
    public ApiResponse<SeatSelectionSummaryResponse> selectionSummary(
            @Valid @RequestBody SeatSelectionRequest request
    ) {
        return ApiResponse.success(seatSelectionService.summarize(request));
    }
}
