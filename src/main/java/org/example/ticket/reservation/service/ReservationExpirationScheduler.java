package org.example.ticket.reservation.service;

import lombok.RequiredArgsConstructor;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;

@Component
@RequiredArgsConstructor
@ConditionalOnProperty(name = "reservation.expiration.scheduler-enabled", havingValue = "true", matchIfMissing = true)
public class ReservationExpirationScheduler {
    private final ReservationExpirationService expirationService;

    @Scheduled(fixedDelayString = "${reservation.expiration.fixed-delay:30s}")
    public void expire() {
        expirationService.expire(LocalDateTime.now(), 1000);
    }
}
