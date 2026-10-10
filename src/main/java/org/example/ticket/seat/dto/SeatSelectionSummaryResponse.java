package org.example.ticket.seat.dto;

import java.util.List;

public record SeatSelectionSummaryResponse(
        Long performanceTimeId,
        List<SeatResponse> seats,
        Integer totalPrice
) {
    public static SeatSelectionSummaryResponse of(Long performanceTimeId, List<SeatResponse> seats) {
        return new SeatSelectionSummaryResponse(
                performanceTimeId,
                List.copyOf(seats),
                seats.stream().mapToInt(SeatResponse::price).sum()
        );
    }
}
